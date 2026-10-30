"""M0-5：最小压测台（小样本）——检索 QPS / P95 与 rerank 段占比（§一 / 附录 C）。"""

from __future__ import annotations

import asyncio
import statistics
import time

import asyncpg
from lightrag import QueryParam
from probes.common import (
    build_rag,
    configure_environment,
    print_summary,
    reset_schema,
    write_report,
)

DOC_COUNT = 200
QUERY_COUNT = 200
CONCURRENCY = 10


def build_document(index: int) -> str:
    region = ["USA", "UK", "JP"][index % 3]
    asset = ["BTC", "ETH", "USDT"][index % 3]
    body = f"知识卡 {index}：{asset} 现货在 {region} 地区的交易规则与风险提示。"
    return body * 90


async def main() -> None:
    settings = configure_environment()
    await reset_schema(settings)
    rag = build_rag(settings, workspace="zh-CN", dim=64, cosine_threshold=-1.0)
    await rag.initialize_storages()
    try:
        seed_start = time.perf_counter()
        batch_size = 50
        for start in range(0, DOC_COUNT, batch_size):
            ids = [f"load-doc-{i:04d}" for i in range(start, min(start + batch_size, DOC_COUNT))]
            docs = [build_document(i) for i in range(start, min(start + batch_size, DOC_COUNT))]
            await rag.apipeline_enqueue_documents(
                docs, ids=ids, file_paths=ids, process_options="F!"
            )
        await rag.apipeline_process_enqueue_documents()
        seed_seconds = time.perf_counter() - seed_start

        conn = await asyncpg.connect(settings.postgres.dsn())
        try:
            chunk_count = await conn.fetchval("SELECT count(*) FROM lightrag_doc_chunks")
        finally:
            await conn.close()

        latencies: list[float] = []
        errors = 0
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def one_query(index: int) -> None:
            nonlocal errors
            async with semaphore:
                start = time.perf_counter()
                try:
                    result = await rag.aquery_data(
                        f"知识卡 {index % DOC_COUNT} 的交易规则",
                        QueryParam(
                            mode="mix",
                            top_k=20,
                            chunk_top_k=10,
                            hl_keywords=["BTC"],
                            ll_keywords=["知识卡"],
                            enable_rerank=False,
                        ),
                    )
                    if result.get("status") != "success":
                        errors += 1
                except Exception:
                    errors += 1
                finally:
                    latencies.append((time.perf_counter() - start) * 1000)

        query_start = time.perf_counter()
        await asyncio.gather(*(one_query(i) for i in range(QUERY_COUNT)))
        elapsed = time.perf_counter() - query_start
    finally:
        await rag.finalize_storages()

    sorted_latencies = sorted(latencies)

    def percentile(p: float) -> float:
        if not sorted_latencies:
            return 0.0
        index = min(len(sorted_latencies) - 1, round((len(sorted_latencies) - 1) * p))
        return sorted_latencies[index]

    payload = {
        "probe": "load",
        "config": {
            "documents": DOC_COUNT,
            "queries": QUERY_COUNT,
            "concurrency": CONCURRENCY,
            "mode": "mix",
            "rerank_enabled": False,
            "external_rerank_share_pct": 0.0,
            "embedding_dim": 64,
            "hardware": "local docker PG18.6 + pgvector0.8.7, host",
        },
        "seed_seconds": round(seed_seconds, 3),
        "chunk_count": chunk_count,
        "error_count": errors,
        "elapsed_seconds": round(elapsed, 3),
        "qps": round(QUERY_COUNT / elapsed, 2),
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(0.50), 2),
            "p95": round(percentile(0.95), 2),
            "p99": round(percentile(0.99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "notes": [
            "rerank 默认关闭，rerank 段占比为 0；"
            "外部 rerank 的出境与延迟须在 M6 白名单确认后另行标定",
            "本基线是同机小样本框架基线，不代表 1000 QPS 容量；M7 用真实数据压测定稿",
            "M0 服务目标（QPS=1000 / P95<=5s）在本机单实例上未达标时，报告给出实测上限而非假设",
        ],
        "passed": errors == 0 and bool(latencies),
    }
    write_report("05-load", payload)
    print_summary(payload)


if __name__ == "__main__":
    asyncio.run(main())
