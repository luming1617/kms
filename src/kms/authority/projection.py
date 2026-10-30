"""A.7 投影层加列（M3 两张 chunk 表；M4 候选四表）。"""

from __future__ import annotations

import re
from types import SimpleNamespace

import asyncpg
from lightrag.base import BaseVectorStorage
from lightrag.kg.postgres_impl import namespace_to_table_name
from lightrag.namespace import NameSpace
from lightrag.utils import EmbeddingFunc

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def collection_suffix(model_name: str, embedding_dim: int) -> str:
    async def _unused(_texts: list[str], **_kwargs: object) -> list[list[float]]:
        return []

    stub = SimpleNamespace(
        embedding_func=EmbeddingFunc(
            embedding_dim=embedding_dim, func=_unused, model_name=model_name
        )
    )
    suffix = BaseVectorStorage._generate_collection_suffix(stub)  # type: ignore[arg-type]
    if not suffix:
        raise ValueError("embedding_model 为空：向量表会回落成裸基表")
    return str(suffix)


def resolve_projection_tables(model_name: str, embedding_dim: int) -> dict[str, str]:
    suffix = collection_suffix(model_name, embedding_dim)
    tables = {
        "doc_chunks": "LIGHTRAG_DOC_CHUNKS",
        "vdb_chunks": f"{namespace_to_table_name(NameSpace.VECTOR_STORE_CHUNKS)}_{suffix}",
        "vdb_entity": f"{namespace_to_table_name(NameSpace.VECTOR_STORE_ENTITIES)}_{suffix}",
        "vdb_relation": f"{namespace_to_table_name(NameSpace.VECTOR_STORE_RELATIONSHIPS)}_{suffix}",
        "graph_nodes": "lightrag_graph_nodes",
        "graph_edges": "lightrag_graph_edges",
    }
    for table in tables.values():
        if not _TABLE_RE.match(table) or len(table) > 63:
            raise ValueError(f"非法投影表名：{table}")
    return tables


def chunk_columns_sql(table: str) -> str:
    return f"""
ALTER TABLE IF EXISTS {table}
ADD COLUMN IF NOT EXISTS product text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS year text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS security_level text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS asset text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS region text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS publish_state text NOT NULL DEFAULT 'PENDING',
ADD COLUMN IF NOT EXISTS valid_from timestamptz NOT NULL DEFAULT '-infinity'::timestamptz,
ADD COLUMN IF NOT EXISTS valid_to timestamptz NULL,
ADD COLUMN IF NOT EXISTS kid_version int NULL,
ADD COLUMN IF NOT EXISTS scope_sig text NULL
"""


def candidate_columns_sql(table: str) -> str:
    return f"""
ALTER TABLE IF EXISTS {table}
ADD COLUMN IF NOT EXISTS product text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS year text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS security_level text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS asset text[] NOT NULL DEFAULT '{{}}'::text[],
ADD COLUMN IF NOT EXISTS region text[] NOT NULL DEFAULT '{{}}'::text[]
"""


async def apply_chunk_projection_schema(
    connection: asyncpg.Connection, *, model_name: str, embedding_dim: int
) -> dict[str, str]:
    tables = resolve_projection_tables(model_name, embedding_dim)
    for key in ("doc_chunks", "vdb_chunks"):
        table = tables[key]
        await connection.execute(chunk_columns_sql(table))
    await connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_dc_scope_gin ON {tables['doc_chunks']} "
        "USING gin (product, year, security_level, asset, region)"
    )
    await connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_vdbc_scope_gin ON {tables['vdb_chunks']} "
        "USING gin (product, year, security_level, asset, region)"
    )
    return tables


async def apply_candidate_projection_schema(
    connection: asyncpg.Connection, *, model_name: str, embedding_dim: int
) -> dict[str, str]:
    tables = resolve_projection_tables(model_name, embedding_dim)
    for key in ("vdb_entity", "vdb_relation", "graph_nodes", "graph_edges"):
        await connection.execute(candidate_columns_sql(tables[key]))
    return tables
