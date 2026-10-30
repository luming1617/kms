"""候选表 scope 反聚合：节点取来源并集、边取两端交集（§8.3 / §12.2）。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import asyncpg

from kms.scope import SET_DIMS

DENY_TOKEN = "__NO_SOURCE__"
GRAPH_NAMESPACE = "chunk_entity_relation"
RELATION_SEP = "<SEP>"


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    import json

    parsed = json.loads(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def deny_scope() -> dict[str, list[str]]:
    return {dim: [DENY_TOKEN] for dim in SET_DIMS}


def union_scope(rows: Iterable[dict[str, list[str]]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {dim: [] for dim in SET_DIMS}
    for row in rows:
        for dim in SET_DIMS:
            result[dim].extend(row.get(dim, []))
    return {dim: sorted(set(values)) for dim, values in result.items()}


def intersect_scope(
    left: dict[str, list[str]] | None, right: dict[str, list[str]] | None
) -> dict[str, list[str]]:
    if left is None or right is None:
        return deny_scope()
    if DENY_TOKEN in left.get(SET_DIMS[0], []) or DENY_TOKEN in right.get(SET_DIMS[0], []):
        return deny_scope()
    return {dim: sorted(set(left.get(dim, [])) & set(right.get(dim, []))) for dim in SET_DIMS}


async def load_current_chunk_scopes(
    connection: asyncpg.Connection, language: str
) -> dict[str, dict[str, list[str]]]:
    rows = await connection.fetch(
        """
        SELECT cf.chunk_id, cf.product, cf.year, cf.security_level, cf.asset, cf.region
        FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid AND k.current_version = cf.kid_version
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED'
        """,
        language,
    )
    return {row["chunk_id"]: {dim: list(row[dim]) for dim in SET_DIMS} for row in rows}


async def _load_tracking(
    connection: asyncpg.Connection, table: str, workspace: str
) -> dict[str, list[str]]:
    rows = await connection.fetch(
        f"SELECT id, chunk_ids FROM {table} WHERE workspace = $1", workspace
    )
    return {str(row["id"]): _json_list(row["chunk_ids"]) for row in rows}


def _aggregate(
    tracking: dict[str, list[str]], chunk_scopes: dict[str, dict[str, list[str]]]
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    aggregated: dict[str, dict[str, list[str]]] = {}
    missing: list[str] = []
    for key, chunk_ids in tracking.items():
        rows = [chunk_scopes[chunk_id] for chunk_id in chunk_ids if chunk_id in chunk_scopes]
        if not rows:
            missing.append(key)
            continue
        aggregated[key] = union_scope(rows)
    return aggregated, missing


async def compute_expected_scopes(
    connection: asyncpg.Connection, *, language: str, workspace: str
) -> dict[str, Any]:
    chunk_scopes = await load_current_chunk_scopes(connection, language)
    entity_tracking = await _load_tracking(connection, "LIGHTRAG_ENTITY_CHUNKS", workspace)
    relation_tracking = await _load_tracking(connection, "LIGHTRAG_RELATION_CHUNKS", workspace)
    node_scopes, missing_nodes = _aggregate(entity_tracking, chunk_scopes)
    edge_scopes: dict[str, dict[str, list[str]]] = {}
    for relation_key in relation_tracking:
        parts = relation_key.split(RELATION_SEP)
        if len(parts) != 2:
            edge_scopes[relation_key] = deny_scope()
            continue
        edge_scopes[relation_key] = intersect_scope(
            node_scopes.get(parts[0]), node_scopes.get(parts[1])
        )
    return {
        "chunk_scopes": chunk_scopes,
        "entity_tracking": entity_tracking,
        "relation_tracking": relation_tracking,
        "node_scopes": node_scopes,
        "edge_scopes": edge_scopes,
        "missing_nodes": missing_nodes,
    }


async def recompute_candidate_scopes(
    connection: asyncpg.Connection, *, language: str, workspace: str
) -> dict[str, int]:
    expected = await compute_expected_scopes(connection, language=language, workspace=workspace)
    node_scopes = expected["node_scopes"]
    edge_scopes = expected["edge_scopes"]

    node_ids = await connection.fetch(
        "SELECT id FROM lightrag_graph_nodes WHERE workspace = $1 AND namespace = $2",
        workspace,
        GRAPH_NAMESPACE,
    )
    node_params = []
    for row in node_ids:
        scope = node_scopes.get(row["id"], deny_scope())
        node_params.append(
            (
                scope["product"],
                scope["year"],
                scope["security_level"],
                scope["asset"],
                scope["region"],
                workspace,
                GRAPH_NAMESPACE,
                row["id"],
            )
        )
    if node_params:
        await connection.executemany(
            """
            UPDATE lightrag_graph_nodes
            SET product = $1::text[], year = $2::text[], security_level = $3::text[],
                asset = $4::text[], region = $5::text[]
            WHERE workspace = $6 AND namespace = $7 AND id = $8
            """,
            node_params,
        )

    edges = await connection.fetch(
        "SELECT src_id, tgt_id FROM lightrag_graph_edges WHERE workspace = $1 AND namespace = $2",
        workspace,
        GRAPH_NAMESPACE,
    )
    edge_params = []
    for row in edges:
        scope = edge_scopes.get(
            RELATION_SEP.join(sorted((row["src_id"], row["tgt_id"]))), deny_scope()
        )
        edge_params.append(
            (
                scope["product"],
                scope["year"],
                scope["security_level"],
                scope["asset"],
                scope["region"],
                workspace,
                GRAPH_NAMESPACE,
                row["src_id"],
                row["tgt_id"],
            )
        )
    if edge_params:
        await connection.executemany(
            """
            UPDATE lightrag_graph_edges
            SET product = $1::text[], year = $2::text[], security_level = $3::text[],
                asset = $4::text[], region = $5::text[]
            WHERE workspace = $6 AND namespace = $7 AND src_id = $8 AND tgt_id = $9
            """,
            edge_params,
        )

    suffix = await _suffix_from_tables(connection, workspace)
    entity_vdb = f"LIGHTRAG_VDB_ENTITY_{suffix}"
    relation_vdb = f"LIGHTRAG_VDB_RELATION_{suffix}"
    entity_rows = await connection.fetch(
        f"SELECT id FROM {entity_vdb} WHERE workspace = $1", workspace
    )
    entity_params = []
    for row in entity_rows:
        scope = node_scopes.get(row["id"], deny_scope())
        entity_params.append(
            (
                scope["product"],
                scope["year"],
                scope["security_level"],
                scope["asset"],
                scope["region"],
                workspace,
                row["id"],
            )
        )
    if entity_params:
        await connection.executemany(
            f"""
            UPDATE {entity_vdb}
            SET product=$1::text[], year=$2::text[], security_level=$3::text[],
                asset=$4::text[], region=$5::text[]
            WHERE workspace=$6 AND id=$7
            """,
            entity_params,
        )
    relation_rows = await connection.fetch(
        f"SELECT source_id, target_id FROM {relation_vdb} WHERE workspace = $1", workspace
    )
    relation_params = []
    for row in relation_rows:
        relation_key = RELATION_SEP.join(sorted((row["source_id"], row["target_id"])))
        scope = edge_scopes.get(relation_key, deny_scope())
        relation_params.append(
            (
                scope["product"],
                scope["year"],
                scope["security_level"],
                scope["asset"],
                scope["region"],
                workspace,
                row["source_id"],
                row["target_id"],
            )
        )
    if relation_params:
        await connection.executemany(
            f"""
            UPDATE {relation_vdb}
            SET product=$1::text[], year=$2::text[], security_level=$3::text[],
                asset=$4::text[], region=$5::text[]
            WHERE workspace=$6 AND source_id=$7 AND target_id=$8
            """,
            relation_params,
        )
    return {
        "nodes": len(node_ids),
        "edges": len(edges),
        "entities": len(entity_rows),
        "relationships": len(relation_rows),
    }


def _scope_equal(actual: Sequence[Any], expected: dict[str, list[str]], dim: str) -> bool:
    return sorted(actual) == expected[dim]


async def candidate_scope_mismatches(
    connection: asyncpg.Connection, *, language: str, workspace: str
) -> tuple[int, list[str]]:
    expected = await compute_expected_scopes(connection, language=language, workspace=workspace)
    node_scopes = expected["node_scopes"]
    edge_scopes = expected["edge_scopes"]
    mismatches: list[str] = []

    nodes = await connection.fetch(
        "SELECT id, product, year, security_level, asset, region FROM lightrag_graph_nodes "
        "WHERE workspace = $1 AND namespace = $2",
        workspace,
        GRAPH_NAMESPACE,
    )
    for row in nodes:
        scope = node_scopes.get(row["id"], deny_scope())
        if any(not _scope_equal(row[dim], scope, dim) for dim in SET_DIMS):
            mismatches.append(f"node:{row['id']}")

    edges = await connection.fetch(
        "SELECT src_id, tgt_id, product, year, security_level, asset, region "
        "FROM lightrag_graph_edges WHERE workspace = $1 AND namespace = $2",
        workspace,
        GRAPH_NAMESPACE,
    )
    for row in edges:
        key = RELATION_SEP.join(sorted((row["src_id"], row["tgt_id"])))
        scope = edge_scopes.get(key, deny_scope())
        if any(not _scope_equal(row[dim], scope, dim) for dim in SET_DIMS):
            mismatches.append(f"edge:{key}")

    suffix = await _suffix_from_tables(connection, workspace)
    entities = await connection.fetch(
        f"SELECT id, product, year, security_level, asset, region "
        f"FROM LIGHTRAG_VDB_ENTITY_{suffix} WHERE workspace = $1",
        workspace,
    )
    for row in entities:
        scope = node_scopes.get(row["id"], deny_scope())
        if any(not _scope_equal(row[dim], scope, dim) for dim in SET_DIMS):
            mismatches.append(f"entity:{row['id']}")
    relations = await connection.fetch(
        f"SELECT source_id, target_id, product, year, security_level, asset, region "
        f"FROM LIGHTRAG_VDB_RELATION_{suffix} WHERE workspace = $1",
        workspace,
    )
    for row in relations:
        key = RELATION_SEP.join(sorted((row["source_id"], row["target_id"])))
        scope = edge_scopes.get(key, deny_scope())
        if any(not _scope_equal(row[dim], scope, dim) for dim in SET_DIMS):
            mismatches.append(f"relation:{key}")
    return len(mismatches), mismatches[:5]


async def _suffix_from_tables(connection: asyncpg.Connection, workspace: str) -> str:
    """从 workspace_registry 解析该语言 embedding_model/dim，拼 VDB 后缀。"""
    from kms.authority.projection import collection_suffix

    row = await connection.fetchrow(
        "SELECT embedding_model, embedding_dim FROM workspace_registry WHERE lang = $1",
        workspace,
    )
    if row is None:
        raise ValueError(f"workspace 未登记：{workspace}")
    return collection_suffix(row["embedding_model"], row["embedding_dim"])
