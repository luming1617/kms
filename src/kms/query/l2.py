"""L2：三条内容通道回查权威快照，fail-closed（§五）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from kms.scope import (
    AUTHORIZATION_COLLECTION_DIMS,
    AUTHORIZATION_FAILURE_DIMS,
    PREFERENCE_DIMS,
    DiscardRecord,
    FilterAST,
    RequestMetrics,
)

RELATION_SEP = "<SEP>"
logger = logging.getLogger(__name__)
CHANNELS = ("chunks", "entities", "relationships")
AUTH_FAILURE_DIMS = AUTHORIZATION_FAILURE_DIMS


@dataclass(frozen=True, slots=True)
class ChunkVerdict:
    chunk_id: str
    passed: bool
    kid: str | None = None
    kid_version: int | None = None
    biz_key: str | None = None
    failed_dims: tuple[str, ...] = ()
    preference_hit: bool = False

    @property
    def auth_failed(self) -> bool:
        return bool(set(self.failed_dims) & AUTH_FAILURE_DIMS)


@dataclass(frozen=True, slots=True)
class ChannelVerdict:
    key: str
    passed: bool
    failed_dims: tuple[str, ...] = ()
    source_chunk_ids: tuple[str, ...] = ()


@dataclass
class L2Result:
    chunks: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    chunk_verdicts: dict[str, ChunkVerdict] = field(default_factory=dict)
    entity_verdicts: dict[str, ChannelVerdict] = field(default_factory=dict)
    relationship_verdicts: dict[str, ChannelVerdict] = field(default_factory=dict)
    aborted_channels: tuple[str, ...] = ()
    entered: dict[str, int] = field(default_factory=dict)
    discarded: dict[str, int] = field(default_factory=dict)

    @property
    def answerable_count(self) -> int:
        return len(self.chunks)


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    parsed = json.loads(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _inter_pref(snapshot: asyncpg.Record, ast: FilterAST) -> bool:
    for dim in PREFERENCE_DIMS:
        values = set(snapshot[dim] or [])
        if values & set(getattr(ast, dim)):
            return True
    return False


def _evaluate_snapshot(snapshot: asyncpg.Record, ast: FilterAST) -> tuple[tuple[str, ...], bool]:
    failed: list[str] = []
    if snapshot["kid_version"] < snapshot["current_version"]:
        failed.append("version_lag")
    if snapshot["publish_state"] != "PUBLISHED" or snapshot["card_state"] != "PUBLISHED":
        failed.append("publish_state")
    for dim in AUTHORIZATION_COLLECTION_DIMS:
        card_values = set(snapshot[dim] or [])
        if card_values and not card_values & set(getattr(ast, dim)):
            failed.append(dim)
    valid_from = snapshot["valid_from"]
    valid_to = snapshot["valid_to"]
    if valid_from is not None and valid_from > ast.valid_at:
        failed.append("validity")
    if valid_to is not None and ast.valid_at >= valid_to:
        failed.append("validity")
    return tuple(failed), _inter_pref(snapshot, ast)


async def _load_snapshots(
    connection: asyncpg.Connection,
    *,
    workspace: str,
    chunk_ids: list[str],
) -> tuple[dict[str, int], dict[str, asyncpg.Record]]:
    if not chunk_ids:
        return {}, {}
    projection_rows = await connection.fetch(
        "SELECT id, kid_version FROM LIGHTRAG_DOC_CHUNKS "
        "WHERE workspace = $1 AND id = ANY($2::text[])",
        workspace,
        chunk_ids,
    )
    versions = {
        str(row["id"]): int(row["kid_version"])
        for row in projection_rows
        if row["kid_version"] is not None
    }
    pairs = [(chunk_id, version) for chunk_id, version in versions.items()]
    if not pairs:
        return versions, {}
    snapshot_rows = await connection.fetch(
        """
        SELECT cf.chunk_id, cf.kid, cf.kid_version, cf.product, cf.year, cf.security_level,
               cf.asset, cf.region, cf.publish_state, cf.valid_from, cf.valid_to,
               k.current_version, k.publish_state AS card_state, k.biz_key
        FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid
        WHERE (cf.chunk_id, cf.kid_version) IN (
            SELECT * FROM unnest($1::text[], $2::int[])
        )
        """,
        [item[0] for item in pairs],
        [item[1] for item in pairs],
    )
    return versions, {str(row["chunk_id"]): row for row in snapshot_rows}


async def _tracking_map(
    connection: asyncpg.Connection, *, workspace: str, table: str, keys: list[str]
) -> dict[str, list[str]]:
    if not keys:
        return {}
    rows = await connection.fetch(
        f"SELECT id, chunk_ids FROM {table} WHERE workspace = $1 AND id = ANY($2::text[])",
        workspace,
        keys,
    )
    return {str(row["id"]): _json_list(row["chunk_ids"]) for row in rows}


async def assert_channels(
    connection: asyncpg.Connection,
    *,
    data: dict[str, Any],
    workspace: str,
    ast: FilterAST,
    metrics: RequestMetrics,
    batch_discard_ratio: float,
) -> L2Result:
    raw_chunks = [chunk for chunk in data.get("chunks", []) if chunk.get("chunk_id")]
    raw_entities = data.get("entities", [])
    raw_relationships = data.get("relationships", [])

    entity_keys = [str(item.get("entity_name", "")) for item in raw_entities]
    relation_keys = [
        RELATION_SEP.join(sorted((str(item.get("src_id", "")), str(item.get("tgt_id", "")))))
        for item in raw_relationships
    ]
    entity_tracking = await _tracking_map(
        connection, workspace=workspace, table="LIGHTRAG_ENTITY_CHUNKS", keys=entity_keys
    )
    relation_tracking = await _tracking_map(
        connection, workspace=workspace, table="LIGHTRAG_RELATION_CHUNKS", keys=relation_keys
    )
    source_ids = {
        chunk_id
        for sources in list(entity_tracking.values()) + list(relation_tracking.values())
        for chunk_id in sources
    }
    all_chunk_ids = sorted({chunk["chunk_id"] for chunk in raw_chunks} | source_ids)
    versions, snapshots = await _load_snapshots(
        connection, workspace=workspace, chunk_ids=all_chunk_ids
    )

    verdicts: dict[str, ChunkVerdict] = {}
    for chunk_id in all_chunk_ids:
        snapshot = snapshots.get(chunk_id)
        if chunk_id not in versions:
            verdicts[chunk_id] = ChunkVerdict(
                chunk_id=chunk_id, passed=False, failed_dims=("missing_projection",)
            )
            continue
        if snapshot is None:
            verdicts[chunk_id] = ChunkVerdict(
                chunk_id=chunk_id, passed=False, failed_dims=("missing_snapshot",)
            )
            continue
        failed, preference_hit = _evaluate_snapshot(snapshot, ast)
        verdicts[chunk_id] = ChunkVerdict(
            chunk_id=chunk_id,
            passed=not failed,
            kid=snapshot["kid"],
            kid_version=int(snapshot["kid_version"]),
            biz_key=snapshot["biz_key"],
            failed_dims=failed,
            preference_hit=preference_hit,
        )

    result = L2Result(
        entered={
            "chunks": len(raw_chunks),
            "entities": len(raw_entities),
            "relationships": len(raw_relationships),
        }
    )
    for chunk in raw_chunks:
        verdict = verdicts[chunk["chunk_id"]]
        if verdict.passed:
            enriched = dict(chunk)
            enriched["kid"] = verdict.kid
            enriched["biz_key"] = verdict.biz_key
            result.chunks.append(enriched)
        else:
            _discard(
                metrics,
                _record("chunks", verdict.chunk_id, verdict.kid, verdict.failed_dims, "l2"),
            )
    result.discarded["chunks"] = result.entered["chunks"] - len(result.chunks)

    result.entity_verdicts = _channel_verdicts(
        raw_entities, entity_keys, entity_tracking, verdicts, "entities", result, metrics
    )
    result.relationship_verdicts = _channel_verdicts(
        raw_relationships,
        relation_keys,
        relation_tracking,
        verdicts,
        "relationships",
        result,
        metrics,
    )
    result.chunk_verdicts = verdicts
    result.aborted_channels = tuple(
        channel
        for channel in CHANNELS
        if result.entered.get(channel, 0) > 0
        and result.discarded.get(channel, 0) / result.entered[channel] > batch_discard_ratio
    )
    if "chunks" in result.aborted_channels:
        # 批次安全事件：整批清空，交给编排层重查一次；重查仍失败则兜底，
        # 绝不把“幸存 chunk”当作可答依据（§5.3 / §9.2）。
        result.chunks = []
        result.discarded["chunks"] = result.entered.get("chunks", 0)
    metrics.l2_entered.update(result.entered)
    metrics.l2_discarded.update(result.discarded)
    return result


def _discard(metrics: RequestMetrics, record: DiscardRecord) -> None:
    metrics.discard(record)
    logger.info(
        "l2_discard channel=%s chunk_id=%s kid=%s dims=%s reason=%s trace_id=%s",
        record.channel,
        record.chunk_id,
        record.kid,
        ",".join(record.failed_dims) or "-",
        record.reason,
        metrics.trace_id or "-",
    )


def _record(
    channel: str, chunk_id: str | None, kid: str | None, failed: tuple[str, ...], reason: str
):
    from kms.scope import DiscardRecord

    return DiscardRecord(
        channel=channel, chunk_id=chunk_id, kid=kid, failed_dims=failed, reason=reason
    )


def _channel_verdicts(
    raw_items: list[dict[str, Any]],
    keys: list[str],
    tracking: dict[str, list[str]],
    chunk_verdicts: dict[str, ChunkVerdict],
    channel: str,
    result: L2Result,
    metrics: RequestMetrics,
) -> dict[str, ChannelVerdict]:
    verdicts: dict[str, ChannelVerdict] = {}
    passed_items: list[dict[str, Any]] = []
    for item, key in zip(raw_items, keys, strict=False):
        sources = tracking.get(key)
        if not sources:
            verdicts[key] = ChannelVerdict(key=key, passed=False, failed_dims=("missing_tracking",))
            _discard(metrics, _record(channel, None, None, ("missing_tracking",), "l2"))
            continue
        failed: set[str] = set()
        for chunk_id in sources:
            chunk_verdict = chunk_verdicts.get(chunk_id)
            if chunk_verdict is None:
                failed.add("missing_snapshot")
            elif not chunk_verdict.passed:
                failed.update(chunk_verdict.failed_dims)
        verdict = ChannelVerdict(
            key=key,
            passed=not failed,
            failed_dims=tuple(sorted(failed)),
            source_chunk_ids=tuple(sources),
        )
        verdicts[key] = verdict
        if verdict.passed:
            passed_items.append(item)
        else:
            _discard(metrics, _record(channel, None, None, verdict.failed_dims, "l2"))
    if channel == "entities":
        result.entities = passed_items
    else:
        result.relationships = passed_items
    result.discarded[channel] = len(raw_items) - len(passed_items)
    return verdicts
