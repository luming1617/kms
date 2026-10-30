"""M8：按 lang 分区 vs 单表的 HNSW 查询/召回对比；输出决策记录。"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from src.kms.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
DIM = 32
ROWS = 20_000
QUERIES = 60
LANGUAGES = ("zh-CN", "en-US", "ja-JP")


def random_vector(rng: random.Random) -> np.ndarray:
    values = np.array([rng.uniform(-1, 1) for _ in range(DIM)], dtype=np.float32)
    norm = float(np.linalg.norm(values)) or 1.0
    return values / norm


async def prepare(conn: asyncpg.Connection, rng: random.Random) -> None:
    await conn.execute("DROP TABLE IF EXISTS m8_single")
    await conn.execute("DROP TABLE IF EXISTS m8_part")
    await conn.execute(
        f"CREATE TABLE m8_single (id int PRIMARY KEY, workspace text, embedding vector({DIM}))"
    )
    await conn.execute(
        f"CREATE TABLE m8_part (id int, workspace text, "
        f"embedding vector({DIM})) PARTITION BY LIST (workspace)"
    )
    for index, language in enumerate(LANGUAGES):
        await conn.execute(
            f"CREATE TABLE m8_part_{index} PARTITION OF m8_part FOR VALUES IN ('{language}')"
        )
    records = [(i, LANGUAGES[i % len(LANGUAGES)], random_vector(rng)) for i in range(ROWS)]
    await conn.copy_records_to_table(
        "m8_single", records=records, columns=["id", "workspace", "embedding"]
    )
    await conn.copy_records_to_table(
        "m8_part", records=records, columns=["id", "workspace", "embedding"]
    )
    await conn.execute(
        "CREATE INDEX m8_single_hnsw ON m8_single USING hnsw (embedding vector_cosine_ops)"
    )
    for index in range(len(LANGUAGES)):
        await conn.execute(
            f"CREATE INDEX m8_part_{index}_hnsw ON m8_part_{index} "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    await conn.execute("ANALYZE m8_single")
    await conn.execute("ANALYZE m8_part")


async def timed_queries(
    conn: asyncpg.Connection, table: str, vectors: list[str]
) -> tuple[list[float], list[set[int]]]:
    latencies: list[float] = []
    result_sets: list[set[int]] = []
    for index, vector in enumerate(vectors):
        language = LANGUAGES[index % len(LANGUAGES)]
        start = time.perf_counter()
        rows = await conn.fetch(
            f"SELECT id FROM {table} WHERE workspace = $1 ORDER BY embedding <=> $2 LIMIT 10",
            language,
            vector,
        )
        latencies.append((time.perf_counter() - start) * 1000)
        result_sets.append({row["id"] for row in rows})
    return latencies, result_sets


async def exact_sets(conn: asyncpg.Connection, vectors: list[str]) -> list[set[int]]:
    await conn.execute("SET LOCAL enable_indexscan = off")
    results = []
    for index, vector in enumerate(vectors):
        rows = await conn.fetch(
            "SELECT id FROM m8_single WHERE workspace = $1 ORDER BY embedding <=> $2 LIMIT 10",
            LANGUAGES[index % len(LANGUAGES)],
            vector,
        )
        results.append({row["id"] for row in rows})
    return results


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * p))]


async def main() -> int:
    settings = load_settings()
    rng = random.Random(20261030)
    conn = await asyncpg.connect(settings.postgres.dsn())
    await register_vector(conn)
    try:
        await prepare(conn, rng)
        vectors = [random_vector(rng) for _ in range(QUERIES)]
        single_latencies, single_sets = await timed_queries(conn, "m8_single", vectors)
        part_latencies, part_sets = await timed_queries(conn, "m8_part", vectors)
        exact = await exact_sets(conn, vectors)

        def recall(results: list[set[int]]) -> float:
            matched = sum(
                len(result & truth) / 10 for result, truth in zip(results, exact, strict=False)
            )
            return matched / len(exact)

        single_recall = recall(single_sets)
        part_recall = recall(part_sets)
        single_p95 = percentile(single_latencies, 0.95)
        part_p95 = percentile(part_latencies, 0.95)
        partition_benefit = part_p95 <= single_p95 * 0.9 and part_recall >= single_recall - 0.01

        decision = {
            "partition_by_language": "enable" if partition_benefit else "disable",
            "self_assembly": "disable",
            "stronger_graph_prefilter": "disable",
            "evidence": {
                "rows": ROWS,
                "dim": DIM,
                "queries": QUERIES,
                "single": {
                    "p50_ms": percentile(single_latencies, 0.5),
                    "p95_ms": single_p95,
                    "recall_at_10": round(single_recall, 4),
                },
                "partition": {
                    "p50_ms": percentile(part_latencies, 0.5),
                    "p95_ms": part_p95,
                    "recall_at_10": round(part_recall, 4),
                },
            },
            "reasons": {
                "partition_by_language": (
                    "分区 P95 未达到单表 90% 以内或召回下降；10k 卡规模下收益不足，"
                    "M8 不启用（附录 C 1/2）"
                    if not partition_benefit
                    else "分区在真实规模下 P95 与召回均达标，可进入灰度"
                ),
                "self_assembly": (
                    "强制点已在存储子类与 L2 落地；自组装不增加可过滤性，只增加维护面（§9.3）"
                ),
                "stronger_graph_prefilter": (
                    "图谱路只承担召回；实体并集语义下更强过滤会同时损害召回与解释性（§2.5）"
                ),
            },
        }
    finally:
        await conn.close()

    output_dir = ROOT / "reports" / "m8"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# M8 规模评估决策记录",
        "",
        f"- 按 lang 分区：**{decision['partition_by_language']}**",
        f"- 自组装检索：**{decision['self_assembly']}**",
        f"- 图谱更强 pre-filter：**{decision['stronger_graph_prefilter']}**",
        "",
        "## 数据依据",
        "",
        f"- 数据量：{ROWS} 行 / dim={DIM} / {QUERIES} 次查询；",
        f"- 单表：P50 {decision['evidence']['single']['p50_ms']:.2f} ms，"
        f"P95 {single_p95:.2f} ms，Recall@10 {single_recall:.4f}；",
        f"- 分区：P50 {decision['evidence']['partition']['p50_ms']:.2f} ms，"
        f"P95 {part_p95:.2f} ms，Recall@10 {part_recall:.4f}。",
        "",
        "## 决策理由",
        "",
        f"- 分区：{decision['reasons']['partition_by_language']}",
        f"- 自组装：{decision['reasons']['self_assembly']}",
        f"- 图谱 pre-filter：{decision['reasons']['stronger_graph_prefilter']}",
        "",
    ]
    (output_dir / "decision.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
