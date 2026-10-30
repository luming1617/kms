"""权威层读写；管理面只查这里，禁止 JOIN / UNION LightRAG 表（§2.6）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from kms.authority.models import CardDraft


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


async def compute_answer_hash(connection: asyncpg.Connection, content: dict[str, Any]) -> str:
    return str(
        await connection.fetchval(
            "SELECT encode(sha256((($1::jsonb)::text)::bytea), 'hex')",
            _json(content),
        )
    )


async def create_card(connection: asyncpg.Connection, draft: CardDraft) -> None:
    await connection.execute(
        """
        INSERT INTO knowledge (
            kid, biz_key, title, schema_version, current_version, publish_state, language,
            product, year, security_level, asset, region, verification_max, action_risk_level,
            valid_from, valid_to, family, locale
        ) VALUES (
            $1, $2, $3, $4, 1, $5, $6,
            $7::text[], $8::text[], $9::text[], $10::text[], $11::text[], $12, $13,
            $14, $15, $16, $17
        )
        """,
        draft.kid,
        draft.biz_key,
        draft.title,
        draft.schema_version,
        draft.publish_state,
        draft.language,
        list(draft.product),
        list(draft.year),
        list(draft.security_level),
        list(draft.asset),
        list(draft.region),
        draft.verification_max,
        draft.action_risk_level,
        draft.valid_from,
        draft.valid_to,
        draft.family,
        draft.locale,
    )
    answer_hash = await compute_answer_hash(connection, draft.content)
    await connection.execute(
        """
        INSERT INTO knowledge_version (
            kid, version, answer_hash, topic, intent_refs, content, answer_policy, quality,
            review_chain, risk_level, timeline_required, timeline_ref, source_refs, owner,
            change_reason, refs, author_id, reviewer_id, is_current, published_at
        ) VALUES (
            $1, 1, $2, $3, $4::text[], $5::jsonb, $6::jsonb, $7::jsonb,
            $8::jsonb, $9, $10, $11, $12::text[], $13,
            $14, $15::jsonb, $16, $17, true, $18
        )
        """,
        draft.kid,
        answer_hash,
        draft.topic or draft.title,
        list(draft.intent_refs),
        _json(draft.content),
        _json(draft.answer_policy),
        _json(draft.quality),
        _json(list(draft.review_chain)),
        draft.risk_level,
        draft.timeline_required,
        draft.timeline_ref,
        list(draft.source_refs),
        draft.owner,
        draft.change_reason,
        _json(list(draft.refs)),
        draft.author_id,
        draft.reviewer_id,
        draft.published_at,
    )


async def add_card_version(
    connection: asyncpg.Connection,
    draft: CardDraft,
    *,
    version: int,
    published_at: datetime | None = None,
) -> str:
    answer_hash = await compute_answer_hash(connection, draft.content)
    await connection.execute(
        "UPDATE knowledge_version SET is_current = false WHERE kid = $1 AND is_current = true",
        draft.kid,
    )
    await connection.execute(
        """
        INSERT INTO knowledge_version (
            kid, version, answer_hash, topic, intent_refs, content, answer_policy, quality,
            review_chain, risk_level, timeline_required, timeline_ref, source_refs, owner,
            change_reason, refs, author_id, reviewer_id, is_current, published_at
        ) VALUES (
            $1, $2, $3, $4, $5::text[], $6::jsonb, $7::jsonb, $8::jsonb,
            $9::jsonb, $10, $11, $12, $13::text[], $14,
            $15, $16::jsonb, $17, $18, true, $19
        )
        """,
        draft.kid,
        version,
        answer_hash,
        draft.topic or draft.title,
        list(draft.intent_refs),
        _json(draft.content),
        _json(draft.answer_policy),
        _json(draft.quality),
        _json(list(draft.review_chain)),
        draft.risk_level,
        draft.timeline_required,
        draft.timeline_ref,
        list(draft.source_refs),
        draft.owner,
        draft.change_reason,
        _json(list(draft.refs)),
        draft.author_id,
        draft.reviewer_id,
        published_at if published_at is not None else draft.published_at,
    )
    await connection.execute(
        """
        UPDATE knowledge SET current_version = $2, title = $3, publish_state = $4,
            product = $5::text[], year = $6::text[], security_level = $7::text[],
            asset = $8::text[], region = $9::text[], valid_from = $10, valid_to = $11,
            updated_at = now()
        WHERE kid = $1
        """,
        draft.kid,
        version,
        draft.title,
        draft.publish_state,
        list(draft.product),
        list(draft.year),
        list(draft.security_level),
        list(draft.asset),
        list(draft.region),
        draft.valid_from,
        draft.valid_to,
    )
    return answer_hash


async def get_card(connection: asyncpg.Connection, kid: str) -> dict[str, Any] | None:
    row = await connection.fetchrow("SELECT * FROM knowledge WHERE kid = $1", kid)
    if row is None:
        return None
    card = dict(row)
    for dim in ("product", "year", "security_level", "asset", "region"):
        card[dim] = list(card[dim])
    return card


async def get_current_version(connection: asyncpg.Connection, kid: str) -> dict[str, Any] | None:
    row = await connection.fetchrow(
        """
        SELECT kv.* FROM knowledge_version kv
        JOIN knowledge k ON k.kid = kv.kid AND k.current_version = kv.version
        WHERE k.kid = $1
        """,
        kid,
    )
    if row is None:
        return None
    version = dict(row)
    for key in ("content", "answer_policy", "quality", "review_chain", "refs"):
        version[key] = _loads(version[key])
    return version


async def set_publish_state(connection: asyncpg.Connection, kid: str, state: str) -> None:
    await connection.execute(
        "UPDATE knowledge SET publish_state = $2, updated_at = now() WHERE kid = $1", kid, state
    )


async def grouping_for_dim(connection: asyncpg.Connection, dim: str) -> str:
    row = await connection.fetchrow("SELECT grouping FROM dim_registry WHERE dim = $1", dim)
    return str(row["grouping"]) if row is not None else "authorization"


async def resolve_relax_order(
    connection: asyncpg.Connection, configured_order: tuple[str, ...]
) -> tuple[str, ...]:
    """阶梯顺序写进配置；只有登记为 preference 的维可进入阶梯，未登记默认 authorization。"""
    allowed: list[str] = []
    for dim in configured_order:
        if await grouping_for_dim(connection, dim) == "preference":
            allowed.append(dim)
    return tuple(allowed)


async def enqueue_conflict(
    connection: asyncpg.Connection,
    *,
    kid: str,
    language: str,
    conflicts: list[str],
) -> None:
    await connection.execute(
        """
        INSERT INTO conflict_review_queue (kid, language, conflicts)
        VALUES ($1, $2, $3::text[])
        """,
        kid,
        language,
        conflicts,
    )


async def current_cards(connection: asyncpg.Connection, language: str) -> list[dict[str, Any]]:
    rows = await connection.fetch(
        """
        SELECT k.*, kv.answer_hash, kv.content, kv.version
        FROM knowledge k
        JOIN knowledge_version kv ON kv.kid = k.kid AND kv.version = k.current_version
        WHERE k.language = $1
        ORDER BY k.kid
        """,
        language,
    )
    result = []
    for row in rows:
        item = dict(row)
        item["content"] = _loads(item["content"])
        for dim in ("product", "year", "security_level", "asset", "region"):
            item[dim] = list(item[dim])
        result.append(item)
    return result


def validity_intersects(
    left_from: datetime,
    left_to: datetime | None,
    right_from: datetime,
    right_to: datetime | None,
) -> bool:
    left_end = left_to
    right_end = right_to
    if left_end is not None and right_from >= left_end:
        return False
    return right_end is None or left_from < right_end
