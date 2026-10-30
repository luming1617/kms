"""chunk_filter 版本行（MERGE）与两张 chunk 表的同源回填（§8.3）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from kms.scope import FilterAST


@dataclass(frozen=True, slots=True)
class ChunkScopeRow:
    chunk_id: str
    kid: str
    kid_version: int
    product: tuple[str, ...]
    year: tuple[str, ...]
    security_level: tuple[str, ...]
    asset: tuple[str, ...]
    region: tuple[str, ...]
    publish_state: str
    valid_from: datetime
    valid_to: datetime | None

    @classmethod
    def from_ast(
        cls,
        *,
        chunk_id: str,
        kid: str,
        kid_version: int,
        ast: FilterAST,
        valid_from: datetime,
        valid_to: datetime | None,
    ) -> ChunkScopeRow:
        return cls(
            chunk_id=chunk_id,
            kid=kid,
            kid_version=kid_version,
            product=ast.product,
            year=ast.year,
            security_level=ast.security_level,
            asset=ast.asset,
            region=ast.region,
            publish_state=ast.publish_state,
            valid_from=valid_from,
            valid_to=valid_to,
        )


async def merge_chunk_filter(connection: asyncpg.Connection, rows: list[ChunkScopeRow]) -> None:
    for row in rows:
        await connection.execute(
            """
            MERGE INTO chunk_filter AS target
            USING (SELECT $1::text AS chunk_id, $2::int AS kid_version) AS source
            ON target.chunk_id = source.chunk_id AND target.kid_version = source.kid_version
            WHEN MATCHED THEN UPDATE SET
                kid = $3, product = $4::text[], year = $5::text[],
                security_level = $6::text[], asset = $7::text[], region = $8::text[],
                publish_state = $9, valid_from = $10, valid_to = $11, updated_at = now()
            WHEN NOT MATCHED THEN INSERT (
                chunk_id, kid, kid_version, product, year, security_level, asset, region,
                publish_state, valid_from, valid_to, scope_sig
            ) VALUES (
                $1, $3, $2, $4::text[], $5::text[], $6::text[], $7::text[], $8::text[],
                $9, $10, $11,
                ics_scope_sig($4::text[], $5::text[], $6::text[], $9, $7::text[], $8::text[])
            )
            """,
            row.chunk_id,
            row.kid_version,
            row.kid,
            list(row.product),
            list(row.year),
            list(row.security_level),
            list(row.asset),
            list(row.region),
            row.publish_state,
            row.valid_from,
            row.valid_to,
        )


async def backfill_chunk_table(
    connection: asyncpg.Connection,
    *,
    table: str,
    workspace: str,
    rows: list[ChunkScopeRow],
) -> int:
    updated = 0
    for row in rows:
        result = await connection.execute(
            f"""
            UPDATE {table}
            SET product = $1::text[], year = $2::text[], security_level = $3::text[],
                asset = $4::text[], region = $5::text[], publish_state = $6,
                valid_from = $7, valid_to = $8, kid_version = $9,
                scope_sig = ics_scope_sig(
                    $1::text[], $2::text[], $3::text[], $6, $4::text[], $5::text[]
                )
            WHERE workspace = $10 AND id = $11
            """,
            list(row.product),
            list(row.year),
            list(row.security_level),
            list(row.asset),
            list(row.region),
            row.publish_state,
            row.valid_from,
            row.valid_to,
            row.kid_version,
            workspace,
            row.chunk_id,
        )
        if result.startswith("UPDATE 1"):
            updated += 1
    return updated


async def backfill_chunk_tables(
    connection: asyncpg.Connection,
    *,
    tables: dict[str, str],
    workspace: str,
    rows: list[ChunkScopeRow],
) -> dict[str, int]:
    return {
        key: await backfill_chunk_table(
            connection, table=tables[key], workspace=workspace, rows=rows
        )
        for key in ("doc_chunks", "vdb_chunks")
    }


async def read_doc_chunk_ids(
    connection: asyncpg.Connection, *, workspace: str, doc_id: str
) -> list[str]:
    rows = await connection.fetch(
        "SELECT id FROM LIGHTRAG_DOC_CHUNKS WHERE workspace = $1 AND full_doc_id = $2 "
        "ORDER BY chunk_order_index",
        workspace,
        doc_id,
    )
    return [str(row["id"]) for row in rows]


async def assert_same_scope(
    connection: asyncpg.Connection,
    *,
    tables: dict[str, str],
    workspace: str,
    chunk_ids: list[str],
) -> list[dict[str, Any]]:
    """两张 chunk 表逐行一致；返回差异行（空列表即平）。"""
    if not chunk_ids:
        return []
    doc_rows = await connection.fetch(
        f"SELECT id, product, year, security_level, asset, region, publish_state, "
        f"valid_from, valid_to, kid_version FROM {tables['doc_chunks']} "
        f"WHERE workspace = $1 AND id = ANY($2::text[]) ORDER BY id",
        workspace,
        chunk_ids,
    )
    vdb_rows = await connection.fetch(
        f"SELECT id, product, year, security_level, asset, region, publish_state, "
        f"valid_from, valid_to, kid_version FROM {tables['vdb_chunks']} "
        f"WHERE workspace = $1 AND id = ANY($2::text[]) ORDER BY id",
        workspace,
        chunk_ids,
    )
    by_id = {row["id"]: row for row in vdb_rows}
    differences: list[dict[str, Any]] = []
    for row in doc_rows:
        other = by_id.get(row["id"])
        if other is None:
            differences.append({"chunk_id": row["id"], "missing_in": "vdb_chunks"})
            continue
        for column in (
            "product",
            "year",
            "security_level",
            "asset",
            "region",
            "publish_state",
            "valid_from",
            "valid_to",
            "kid_version",
        ):
            if row[column] != other[column]:
                differences.append(
                    {
                        "chunk_id": row["id"],
                        "column": column,
                        "doc": row[column],
                        "vdb": other[column],
                    }
                )
    return differences
