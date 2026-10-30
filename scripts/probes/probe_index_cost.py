"""M0-4：LIGHTRAG_DOC_CHUNKS 增列 + text[] GIN 的建索引代价（附录 C 3 / A.7）。"""

from __future__ import annotations

import json
import time

import asyncpg
from probes.common import (
    build_rag,
    configure_environment,
    print_summary,
    reset_schema,
    write_report,
)

ROW_COUNT = 100_000
CONTENT = "现货交易规则与风险提示。" * 60  # 约 1KB

ADD_COLUMNS_SQL = """
ALTER TABLE lightrag_doc_chunks
ADD COLUMN IF NOT EXISTS product text[] NOT NULL DEFAULT '{}'::text[],
ADD COLUMN IF NOT EXISTS year text[] NOT NULL DEFAULT '{}'::text[],
ADD COLUMN IF NOT EXISTS security_level text[] NOT NULL DEFAULT '{}'::text[],
ADD COLUMN IF NOT EXISTS asset text[] NOT NULL DEFAULT '{}'::text[],
ADD COLUMN IF NOT EXISTS region text[] NOT NULL DEFAULT '{}'::text[],
ADD COLUMN IF NOT EXISTS publish_state text NOT NULL DEFAULT 'PENDING',
ADD COLUMN IF NOT EXISTS valid_from timestamptz NOT NULL DEFAULT '-infinity'::timestamptz,
ADD COLUMN IF NOT EXISTS valid_to timestamptz NULL,
ADD COLUMN IF NOT EXISTS kid_version int NULL,
ADD COLUMN IF NOT EXISTS scope_sig text NULL
"""

BACKFILL_SQL = """
UPDATE lightrag_doc_chunks
SET product = ARRAY['SPOT']::text[],
    year = ARRAY['2024']::text[],
    security_level = ARRAY['Secret']::text[],
    asset = ARRAY['BTC','ETH']::text[],
    region = ARRAY['USA','UK']::text[],
    publish_state = 'PUBLISHED',
    valid_from = now() - interval '1 day',
    kid_version = 1
"""

CREATE_GIN_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_dc_scope_gin ON lightrag_doc_chunks "
    "USING gin (product, year, security_level, asset, region)"
)


async def relation_size(conn: asyncpg.Connection, table: str) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        SELECT pg_relation_size($1) AS heap,
               pg_indexes_size($1) AS indexes,
               pg_total_relation_size($1) AS total
        """,
        table,
    )
    return {"heap_bytes": row["heap"], "index_bytes": row["indexes"], "total_bytes": row["total"]}


async def main() -> None:
    settings = configure_environment()
    await reset_schema(settings)
    rag = build_rag(settings, workspace="zh-CN")
    await rag.initialize_storages()
    await rag.finalize_storages()

    conn = await asyncpg.connect(settings.postgres.dsn())
    try:
        before = await relation_size(conn, "lightrag_doc_chunks")
        records = [
            (
                f"cost-{i:06d}",
                "zh-CN",
                f"cost-doc-{i // 5:06d}",
                i % 5,
                320,
                CONTENT,
                "cost",
            )
            for i in range(ROW_COUNT)
        ]
        insert_start = time.perf_counter()
        await conn.copy_records_to_table(
            "lightrag_doc_chunks",
            records=records,
            columns=[
                "id",
                "workspace",
                "full_doc_id",
                "chunk_order_index",
                "tokens",
                "content",
                "file_path",
            ],
        )
        insert_seconds = time.perf_counter() - insert_start

        after_insert = await relation_size(conn, "lightrag_doc_chunks")

        ddl_start = time.perf_counter()
        await conn.execute(ADD_COLUMNS_SQL)
        ddl_seconds = time.perf_counter() - ddl_start
        after_ddl = await relation_size(conn, "lightrag_doc_chunks")

        backfill_start = time.perf_counter()
        await conn.execute(BACKFILL_SQL)
        backfill_seconds = time.perf_counter() - backfill_start

        gin_start = time.perf_counter()
        await conn.execute(CREATE_GIN_SQL)
        gin_seconds = time.perf_counter() - gin_start
        await conn.execute("ANALYZE lightrag_doc_chunks")
        after_gin = await relation_size(conn, "lightrag_doc_chunks")

        explain = await conn.fetch(
            "EXPLAIN (FORMAT JSON) SELECT id FROM lightrag_doc_chunks "
            "WHERE workspace='zh-CN' AND "
            "('{}'::text[] = '{}'::text[] OR product && ARRAY['SPOT']::text[])"
        )
        plan = json.loads(explain[0][0])
        plan_text = json.dumps(plan)
    finally:
        await conn.close()

    payload = {
        "probe": "index_cost",
        "row_count": ROW_COUNT,
        "content_bytes_per_row": len(CONTENT.encode("utf-8")),
        "timings_seconds": {
            "copy_insert": round(insert_seconds, 3),
            "add_columns_ddl": round(ddl_seconds, 3),
            "backfill_100k_rows": round(backfill_seconds, 3),
            "create_gin": round(gin_seconds, 3),
        },
        "sizes": {
            "before_insert": before,
            "after_insert": after_insert,
            "after_ddl": after_ddl,
            "after_gin": after_gin,
        },
        "ddl_rewrote_heap": after_ddl["heap_bytes"] > after_insert["heap_bytes"] * 1.1,
        "gin_index_bytes": after_gin["index_bytes"] - after_ddl["index_bytes"],
        "explain_mentions_seq_scan": "Seq Scan" in plan_text,
        "notes": [
            "恒定默认值（'{}'::text[] / 'PENDING' / '-infinity'）"
            "在 PG18 上是元数据级 ADD COLUMN，不重写堆",
            "GIN 索引在 10 万行、五列均为非空数组时测量；索引体积随数组基数增长",
            "回填耗时单列，不属于 DDL；发布流水线需按此估算维护窗口",
        ],
        "passed": ddl_seconds < 5.0
        and not after_ddl["heap_bytes"] > after_insert["heap_bytes"] * 1.1,
    }
    write_report("04-index-cost", payload)
    print_summary(payload)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
