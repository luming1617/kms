"""M0-3：默认路径 chunk_id 形态与 aquery_data 输出契约（附录 C 6 / §5.4）。"""

from __future__ import annotations

import re

import asyncpg
from lightrag import QueryParam
from lightrag.utils_pipeline import build_chunks_dict_from_chunking_result
from probes.common import (
    build_rag,
    configure_environment,
    print_summary,
    reset_schema,
    write_report,
)

DOC_ID = "probe-doc-1"
CHUNK_PATTERN = re.compile(rf"^{re.escape(DOC_ID)}-chunk-\d{{3}}$")


def static_contract() -> dict:
    chunking_result = [
        {"content": "第一段正文", "chunk_order_index": 0},
        {"content": "第二段正文", "chunk_order_index": 1},
        {"content": "第三段正文", "chunk_order_index": 7},
    ]
    chunks = build_chunks_dict_from_chunking_result(
        chunking_result, doc_id=DOC_ID, file_path=DOC_ID
    )
    keys = list(chunks)
    return {
        "keys": keys,
        "all_match": all(CHUNK_PATTERN.match(key) for key in keys),
        "positional": keys == [f"{DOC_ID}-chunk-000", f"{DOC_ID}-chunk-001", f"{DOC_ID}-chunk-007"],
        "note": "位置型 chunk_id 与内容无关；改版且 chunk 数不变时会复用",
    }


async def runtime_contract() -> dict:
    settings = configure_environment()
    await reset_schema(settings)
    rag = build_rag(settings, workspace="zh-CN")
    await rag.initialize_storages()
    try:
        body = "现货交易规则：BTC 现货在 USA 地区可交易。" * 40
        await rag.apipeline_enqueue_documents(
            [body], ids=[DOC_ID], file_paths=[DOC_ID], process_options="F!"
        )
        await rag.apipeline_process_enqueue_documents()

        conn = await asyncpg.connect(settings.postgres.dsn())
        try:
            rows = await conn.fetch(
                "SELECT id, full_doc_id, chunk_order_index FROM lightrag_doc_chunks "
                "WHERE workspace=$1 ORDER BY chunk_order_index",
                "zh-CN",
            )
        finally:
            await conn.close()

        ids = [row["id"] for row in rows]
        query_result = await rag.aquery_data(
            "BTC 现货 USA",
            QueryParam(
                mode="mix",
                top_k=10,
                chunk_top_k=10,
                hl_keywords=["BTC"],
                ll_keywords=["BTC"],
                enable_rerank=False,
            ),
        )
        data = query_result.get("data", {}) if query_result.get("status") == "success" else {}
        chunks = data.get("chunks", [])
        return {
            "db_ids": ids,
            "db_ids_match": bool(ids) and all(CHUNK_PATTERN.match(chunk_id) for chunk_id in ids),
            "db_orders": [row["chunk_order_index"] for row in rows],
            "full_doc_id_equals_doc_id": all(row["full_doc_id"] == DOC_ID for row in rows),
            "query_status": query_result.get("status"),
            "query_chunk_count": len(chunks),
            "query_chunk_has_chunk_id": bool(chunks) and all("chunk_id" in c for c in chunks),
            "query_chunk_has_content": bool(chunks) and all("content" in c for c in chunks),
            "query_has_references_key": "references" in data,
            "references_sample": data.get("references", [])[:2],
            "result_keys": sorted(data.keys()),
            "metadata": query_result.get("metadata"),
        }
    finally:
        await rag.finalize_storages()


async def main() -> None:
    static = static_contract()
    runtime = await runtime_contract()
    passed = (
        static["all_match"]
        and static["positional"]
        and runtime["db_ids_match"]
        and runtime["full_doc_id_equals_doc_id"]
        and runtime["query_status"] == "success"
        and runtime["query_chunk_has_chunk_id"]
        and runtime["query_has_references_key"]
    )
    payload = {
        "probe": "chunk_ids",
        "static": static,
        "runtime": runtime,
        "deviations": [
            "本机安装的 1.5.7 构建把 process_options 放在 apipeline_enqueue_documents，"
            "apipeline_process_enqueue_documents 不接受该参数"
            "（方案 §8.3 的调用写法需按实际签名执行）",
        ],
        "passed": passed,
    }
    write_report("03-chunk-ids", payload)
    print_summary(payload)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
