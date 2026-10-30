"""发布闸：草稿校验 + 同语言逐维重叠冲突检测（§8.1 ①②③）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncpg

from kms.authority.models import CardDraft
from kms.authority.repository import compute_answer_hash
from kms.config import SECURITY_LEVEL_READ_SETS
from kms.scope import AUTHORIZATION_COLLECTION_DIMS, PREFERENCE_DIMS

_YEAR = re.compile(r"^\d{4}$")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Conflict:
    kid: str
    answer_hash: str


async def validate_language(connection: asyncpg.Connection, language: str) -> list[ValidationIssue]:
    row = await connection.fetchrow(
        "SELECT enabled FROM workspace_registry WHERE lang = $1", language
    )
    if row is None:
        return [ValidationIssue("unknown_language", f"语言未登记：{language}")]
    if not row["enabled"]:
        return [ValidationIssue("disabled_language", f"语言已停用：{language}")]
    return []


async def validate_draft(connection: asyncpg.Connection, draft: CardDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for dim in AUTHORIZATION_COLLECTION_DIMS:
        if not draft.array(dim):
            issues.append(ValidationIssue("auth_dim_empty", f"{dim} 不得为空"))
    if draft.publish_state != "PUBLISHED":
        issues.append(ValidationIssue("not_published", "发布阶段只接受 PUBLISHED 草稿"))

    values: list[tuple[str, str]] = []
    for dim in AUTHORIZATION_COLLECTION_DIMS + PREFERENCE_DIMS:
        for code in draft.array(dim):
            values.append((dim, code))
    if values:
        rows = await connection.fetch(
            """
            SELECT dim, code, enabled FROM dim_scope_value
            WHERE (dim, code) IN (SELECT * FROM unnest($1::text[], $2::text[]))
            """,
            [item[0] for item in values],
            [item[1] for item in values],
        )
        allowed = {(row["dim"], row["code"]) for row in rows if row["enabled"]}
        known = {(row["dim"], row["code"]) for row in rows}
        for dim, code in values:
            if (dim, code) not in known:
                issues.append(ValidationIssue("scope_value_unknown", f"{dim}={code} 不在字典"))
            elif (dim, code) not in allowed:
                issues.append(ValidationIssue("scope_value_disabled", f"{dim}={code} 已停用"))

    for level in draft.security_level:
        if level not in SECURITY_LEVEL_READ_SETS:
            issues.append(ValidationIssue("security_level_invalid", f"未知密级：{level}"))
    for year in draft.year:
        if not _YEAR.match(year):
            issues.append(ValidationIssue("year_invalid", f"年份必须是 4 位代码：{year}"))

    if draft.valid_to is not None and draft.valid_to <= draft.valid_from:
        issues.append(ValidationIssue("window_invalid", "valid_to 必须晚于 valid_from"))
    if draft.valid_to is not None:
        now = await connection.fetchval("SELECT now()")
        if draft.valid_to <= now:
            issues.append(ValidationIssue("window_expired", "时间窗已整体过期"))

    issues.extend(await validate_language(connection, draft.language))
    return issues


async def find_conflicts(connection: asyncpg.Connection, draft: CardDraft) -> list[Conflict]:
    """同语言、六维逐维相交、有效期相交、去前缀正文哈希不同 → 冲突。"""
    draft_hash = await compute_answer_hash(connection, draft.content)
    rows = await connection.fetch(
        """
        SELECT k.kid, kv.answer_hash
        FROM knowledge k
        JOIN knowledge_version kv ON kv.kid = k.kid AND kv.version = k.current_version
        WHERE k.language = $1
          AND k.publish_state = $2
          AND k.kid <> $3
          AND (cardinality(k.product) = 0 OR cardinality($4::text[]) = 0 OR k.product && $4::text[])
          AND (cardinality(k.year) = 0 OR cardinality($5::text[]) = 0 OR k.year && $5::text[])
          AND (
              cardinality(k.security_level) = 0 OR cardinality($6::text[]) = 0
              OR k.security_level && $6::text[]
          )
          AND (cardinality(k.asset) = 0 OR cardinality($7::text[]) = 0 OR k.asset && $7::text[])
          AND (cardinality(k.region) = 0 OR cardinality($8::text[]) = 0 OR k.region && $8::text[])
          AND ($9::timestamptz IS NULL OR k.valid_from < $9::timestamptz)
          AND (k.valid_to IS NULL OR $10::timestamptz < k.valid_to)
        """,
        draft.language,
        draft.publish_state,
        draft.kid,
        list(draft.product),
        list(draft.year),
        list(draft.security_level),
        list(draft.asset),
        list(draft.region),
        draft.valid_to,
        draft.valid_from,
    )
    return [
        Conflict(kid=row["kid"], answer_hash=row["answer_hash"])
        for row in rows
        if row["answer_hash"] != draft_hash
    ]


async def validate_publish_gate(
    connection: asyncpg.Connection, draft: CardDraft
) -> tuple[list[ValidationIssue], list[Conflict]]:
    return await validate_draft(connection, draft), await find_conflicts(connection, draft)
