"""§12.2 对账：核心五项 + 补充巡检；对账只读权威层，修复只写投影层。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import asyncpg
from lightrag.constants import RELATION_NO_EVIDENCE_SOURCE_IDS

from kms.authority.projection import resolve_projection_tables
from kms.publish.backfill import assert_same_scope
from kms.publish.freeze import freeze_language, is_frozen, unfreeze_language
from kms.publish.graph_scope import (
    GRAPH_NAMESPACE,
    RELATION_SEP,
    candidate_scope_mismatches,
    compute_expected_scopes,
)
from kms.publish.ids import strip_knowledge_prefix
from kms.publish.render import render_content

CORE_CRITERIA = (
    "chunk_count_mismatch",
    "two_tables_scope_diff",
    "version_lag",
    "pending_rows",
    "dangling_current_chunks",
)
SUPPLEMENTARY_CRITERIA = (
    "duplicate_pointer",
    "render_drift",
    "candidate_scope_mismatch",
    "tracking_view_diff",
)


@dataclass(frozen=True, slots=True)
class CriterionResult:
    name: str
    count: int
    samples: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.count == 0


@dataclass(frozen=True, slots=True)
class LanguageReport:
    language: str
    criteria: dict[str, CriterionResult]
    frozen: bool

    @property
    def ok(self) -> bool:
        return all(criterion.ok for criterion in self.criteria.values())


def _criterion(
    name: str, count: int, samples: list[str] | None = None, note: str = ""
) -> CriterionResult:
    return CriterionResult(name=name, count=int(count), samples=samples or [], note=note)


async def _tables(connection: asyncpg.Connection, language: str) -> dict[str, str]:
    row = await connection.fetchrow(
        "SELECT embedding_model, embedding_dim FROM workspace_registry WHERE lang = $1",
        language,
    )
    if row is None:
        raise ValueError(f"workspace 未登记：{language}")
    return resolve_projection_tables(row["embedding_model"], row["embedding_dim"])


async def _core_criteria(
    connection: asyncpg.Connection, language: str, tables: dict[str, str]
) -> dict[str, CriterionResult]:
    doc_table = tables["doc_chunks"]
    vdb_table = tables["vdb_chunks"]

    projection_count = await connection.fetchval(
        f"SELECT count(*) FROM {doc_table} WHERE workspace = $1", language
    )
    authority_count = await connection.fetchval(
        """
        SELECT count(*) FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid AND k.current_version = cf.kid_version
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED'
        """,
        language,
    )
    count_mismatch = abs(int(projection_count) - int(authority_count))

    scope_diff = await connection.fetchval(
        f"""
        WITH doc AS (
            SELECT id, product, year, security_level, asset, region, publish_state,
                   valid_from, valid_to, kid_version
            FROM {doc_table} WHERE workspace = $1
        ), vdb AS (
            SELECT id, product, year, security_level, asset, region, publish_state,
                   valid_from, valid_to, kid_version
            FROM {vdb_table} WHERE workspace = $1
        )
        SELECT count(*) FROM (
            (SELECT * FROM doc EXCEPT SELECT * FROM vdb)
            UNION ALL
            (SELECT * FROM vdb EXCEPT SELECT * FROM doc)
        ) AS diff
        """,
        language,
    )

    lag = await connection.fetchval(
        f"""
        SELECT count(*) FROM {doc_table} p
        JOIN knowledge k ON k.kid = p.full_doc_id
        WHERE p.workspace = $1 AND k.publish_state = 'PUBLISHED'
          AND (p.kid_version IS NULL OR p.kid_version < k.current_version)
        """,
        language,
    )
    pending = await connection.fetchval(
        f"""
        SELECT
          (SELECT count(*) FROM {doc_table}
           WHERE workspace = $1 AND publish_state = 'PENDING')
        + (SELECT count(*) FROM {vdb_table}
           WHERE workspace = $1 AND publish_state = 'PENDING')
        """,
        language,
    )
    dangling_rows = await connection.fetch(
        f"""
        SELECT cf.chunk_id FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid AND k.current_version = cf.kid_version
        LEFT JOIN {doc_table} p ON p.workspace = $1 AND p.id = cf.chunk_id
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED' AND p.id IS NULL
        LIMIT 5
        """,
        language,
    )
    dangling = await connection.fetchval(
        f"""
        SELECT count(*) FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid AND k.current_version = cf.kid_version
        LEFT JOIN {doc_table} p ON p.workspace = $1 AND p.id = cf.chunk_id
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED' AND p.id IS NULL
        """,
        language,
    )
    return {
        "chunk_count_mismatch": _criterion("chunk_count_mismatch", count_mismatch),
        "two_tables_scope_diff": _criterion("two_tables_scope_diff", int(scope_diff or 0)),
        "version_lag": _criterion("version_lag", int(lag or 0)),
        "pending_rows": _criterion("pending_rows", int(pending or 0)),
        "dangling_current_chunks": _criterion(
            "dangling_current_chunks",
            int(dangling or 0),
            [row["chunk_id"] for row in dangling_rows],
        ),
    }


async def _duplicate_pointer(connection: asyncpg.Connection, language: str) -> CriterionResult:
    rows = await connection.fetch(
        """
        SELECT ds.id FROM LIGHTRAG_DOC_STATUS ds
        JOIN knowledge k ON k.kid = ds.id
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED'
          AND ds.metadata->>'is_duplicate' = 'true'
        LIMIT 5
        """,
        language,
    )
    count = await connection.fetchval(
        """
        SELECT count(*) FROM LIGHTRAG_DOC_STATUS ds
        JOIN knowledge k ON k.kid = ds.id
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED'
          AND ds.metadata->>'is_duplicate' = 'true'
        """,
        language,
    )
    return _criterion("duplicate_pointer", int(count or 0), [row["id"] for row in rows])


async def _render_drift(connection: asyncpg.Connection, language: str) -> CriterionResult:
    rows = await connection.fetch(
        """
        SELECT k.kid, kv.content, df.content AS projected
        FROM knowledge k
        JOIN knowledge_version kv ON kv.kid = k.kid AND kv.version = k.current_version
        LEFT JOIN LIGHTRAG_DOC_FULL df
               ON df.workspace = k.language AND df.id = k.kid
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED'
        """,
        language,
    )
    drifted: list[str] = []
    for row in rows:
        content = row["content"]
        if isinstance(content, str):
            import json

            content = json.loads(content)
        expected = strip_knowledge_prefix(render_content(content))
        projected = strip_knowledge_prefix(row["projected"] or "").strip()
        if projected != expected.strip():
            drifted.append(row["kid"])
    return _criterion("render_drift", len(drifted), drifted[:5])


def _source_ids(properties: object) -> set[str]:
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except json.JSONDecodeError:
            return set()
    if not isinstance(properties, dict):
        return set()
    raw = properties.get("source_id", "")
    parts = raw if isinstance(raw, list) else str(raw).split(RELATION_SEP)
    return {
        str(part) for part in parts if part and str(part) not in RELATION_NO_EVIDENCE_SOURCE_IDS
    }


async def _tracking_source_diff(
    connection: asyncpg.Connection,
    *,
    expected: dict,
    truncation_limit: int = 200,
) -> tuple[int, list[str], int]:
    """图 source_id（可能被 cap）与 tracking 清单的未解释差异。"""
    entity_tracking = expected["entity_tracking"]
    relation_tracking = expected["relation_tracking"]
    count = 0
    samples: list[str] = []
    truncated = 0
    nodes = await connection.fetch(
        "SELECT id, properties FROM lightrag_graph_nodes WHERE workspace = $1 AND namespace = $2",
        expected["workspace"],
        GRAPH_NAMESPACE,
    )
    for row in nodes:
        tracked = set(entity_tracking.get(row["id"], []))
        source = _source_ids(row["properties"])
        missing = tracked - source
        extra = source - tracked
        if missing and len(source) >= truncation_limit:
            truncated += 1
            missing = set()
        if missing or extra:
            count += len(missing) + len(extra)
            samples.append(f"entity:{row['id']}")
    edges = await connection.fetch(
        "SELECT src_id, tgt_id, properties FROM lightrag_graph_edges "
        "WHERE workspace = $1 AND namespace = $2",
        expected["workspace"],
        GRAPH_NAMESPACE,
    )
    for row in edges:
        key = RELATION_SEP.join(sorted((row["src_id"], row["tgt_id"])))
        tracked = set(relation_tracking.get(key, []))
        source = _source_ids(row["properties"])
        missing = tracked - source
        extra = source - tracked
        if missing and len(source) >= truncation_limit:
            truncated += 1
            missing = set()
        if missing or extra:
            count += len(missing) + len(extra)
            samples.append(f"edge:{key}")
    return count, samples[:5], truncated


async def _tracking_view(
    connection: asyncpg.Connection, language: str, tables: dict[str, str]
) -> CriterionResult:
    expected = await compute_expected_scopes(connection, language=language, workspace=language)
    expected["workspace"] = language
    nodes = await connection.fetch(
        "SELECT id FROM lightrag_graph_nodes WHERE workspace = $1 AND namespace = $2",
        language,
        GRAPH_NAMESPACE,
    )
    edges = await connection.fetch(
        "SELECT src_id, tgt_id FROM lightrag_graph_edges WHERE workspace = $1 AND namespace = $2",
        language,
        GRAPH_NAMESPACE,
    )
    node_ids = {row["id"] for row in nodes}
    edge_keys = {RELATION_SEP.join(sorted((row["src_id"], row["tgt_id"]))) for row in edges}
    tracking_nodes = set(expected["entity_tracking"])
    tracking_edges = set(expected["relation_tracking"])
    id_diff = (
        (tracking_nodes - node_ids)
        | (node_ids - tracking_nodes)
        | (tracking_edges - edge_keys)
        | (edge_keys - tracking_edges)
    )
    source_count, samples, truncated = await _tracking_source_diff(connection, expected=expected)
    return _criterion(
        "tracking_view_diff",
        len(id_diff) + source_count,
        sorted(id_diff)[:5] + samples,
        note=f"truncated={truncated}" if truncated else "",
    )


async def reconcile_card(
    connection: asyncpg.Connection,
    kid: str,
    *,
    freeze_on_failure: bool = True,
) -> LanguageReport:
    """发布后的单卡增量对账（§12.2“每次发布后该卡增量”）。"""
    card = await connection.fetchrow(
        "SELECT language, publish_state, current_version FROM knowledge WHERE kid = $1", kid
    )
    if card is None:
        raise ValueError(f"卡片不存在：{kid}")
    language = str(card["language"])
    tables = await _tables(connection, language)
    doc_table = tables["doc_chunks"]
    criteria: dict[str, CriterionResult] = {}
    projection_count = await connection.fetchval(
        f"SELECT count(*) FROM {doc_table} WHERE workspace = $1 AND full_doc_id = $2",
        language,
        kid,
    )
    pending = await connection.fetchval(
        f"SELECT count(*) FROM {doc_table} WHERE workspace = $1 AND full_doc_id = $2 "
        "AND publish_state = 'PENDING'",
        language,
        kid,
    )
    criteria["pending_rows"] = _criterion("pending_rows", int(pending or 0))
    if card["publish_state"] == "PUBLISHED":
        current = int(card["current_version"])
        chunk_ids = [
            str(row["chunk_id"])
            for row in await connection.fetch(
                "SELECT chunk_id FROM chunk_filter WHERE kid = $1 AND kid_version = $2",
                kid,
                current,
            )
        ]
        criteria["chunk_count_mismatch"] = _criterion(
            "chunk_count_mismatch", abs(len(chunk_ids) - int(projection_count or 0))
        )
        differences = await assert_same_scope(
            connection, tables=tables, workspace=language, chunk_ids=chunk_ids
        )
        criteria["two_tables_scope_diff"] = _criterion(
            "two_tables_scope_diff", len(differences), [str(item) for item in differences[:5]]
        )
        lag = await connection.fetchval(
            f"SELECT count(*) FROM {doc_table} p JOIN knowledge k ON k.kid = p.full_doc_id "
            "WHERE p.workspace = $1 AND p.full_doc_id = $2 AND k.publish_state = 'PUBLISHED' "
            "AND (p.kid_version IS NULL OR p.kid_version < k.current_version)",
            language,
            kid,
        )
        criteria["version_lag"] = _criterion("version_lag", int(lag or 0))
        dangling = await connection.fetchval(
            "SELECT count(*) FROM chunk_filter cf "
            f"LEFT JOIN {doc_table} p ON p.workspace = $1 AND p.id = cf.chunk_id "
            "WHERE cf.kid = $2 AND cf.kid_version = $3 AND p.id IS NULL",
            language,
            kid,
            current,
        )
        criteria["dangling_current_chunks"] = _criterion(
            "dangling_current_chunks", int(dangling or 0)
        )
        duplicate = await connection.fetchval(
            "SELECT count(*) FROM LIGHTRAG_DOC_STATUS "
            "WHERE id = $1 AND metadata->>'is_duplicate' = 'true'",
            kid,
        )
        criteria["duplicate_pointer"] = _criterion("duplicate_pointer", int(duplicate or 0))
        drift = await _render_drift_card(connection, kid)
        criteria["render_drift"] = _criterion("render_drift", drift)
    else:
        criteria["chunk_count_mismatch"] = _criterion(
            "chunk_count_mismatch", int(projection_count or 0)
        )
        criteria["two_tables_scope_diff"] = _criterion("two_tables_scope_diff", 0)
        criteria["version_lag"] = _criterion("version_lag", 0)
        criteria["dangling_current_chunks"] = _criterion("dangling_current_chunks", 0)
    frozen = await is_frozen(connection, language)
    core_failed = any(not criteria[name].ok for name in CORE_CRITERIA if name in criteria)
    if core_failed and freeze_on_failure:
        reason = ", ".join(
            f"{name}={criterion.count}" for name, criterion in criteria.items() if not criterion.ok
        )
        await freeze_language(connection, language, reason)
        frozen = True
    return LanguageReport(language=language, criteria=criteria, frozen=frozen)


async def _render_drift_card(connection: asyncpg.Connection, kid: str) -> int:
    row = await connection.fetchrow(
        "SELECT kv.content, df.content AS projected FROM knowledge k "
        "JOIN knowledge_version kv ON kv.kid = k.kid AND kv.version = k.current_version "
        "LEFT JOIN LIGHTRAG_DOC_FULL df ON df.workspace = k.language AND df.id = k.kid "
        "WHERE k.kid = $1 AND k.publish_state = 'PUBLISHED'",
        kid,
    )
    if row is None:
        return 0
    content = row["content"]
    if isinstance(content, str):
        content = json.loads(content)
    expected = strip_knowledge_prefix(render_content(content)).strip()
    projected = strip_knowledge_prefix(row["projected"] or "").strip()
    return 0 if projected == expected else 1


async def reconcile_language(
    connection: asyncpg.Connection,
    language: str,
    *,
    freeze_on_failure: bool = True,
) -> LanguageReport:
    tables = await _tables(connection, language)
    criteria = await _core_criteria(connection, language, tables)
    criteria["duplicate_pointer"] = await _duplicate_pointer(connection, language)
    criteria["render_drift"] = await _render_drift(connection, language)
    mismatch_count, samples = await candidate_scope_mismatches(
        connection, language=language, workspace=language
    )
    criteria["candidate_scope_mismatch"] = _criterion(
        "candidate_scope_mismatch", mismatch_count, samples
    )
    criteria["tracking_view_diff"] = await _tracking_view(connection, language, tables)
    frozen = await is_frozen(connection, language)
    core_failed = any(not criteria[name].ok for name in CORE_CRITERIA if name in criteria)
    if core_failed and freeze_on_failure:
        reason = ", ".join(
            f"{name}={criterion.count}"
            for name, criterion in criteria.items()
            if not criterion.ok and name in CORE_CRITERIA
        )
        await freeze_language(connection, language, reason)
        frozen = True
    elif freeze_on_failure and not core_failed and frozen:
        # 核心判据恢复：解冻发布队列；补充巡检不阻塞发布（§12.2）。
        await unfreeze_language(connection, language)
        frozen = False
    return LanguageReport(language=language, criteria=criteria, frozen=frozen)
