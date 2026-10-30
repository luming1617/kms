"""L3：短缺归因与松弛阶梯；只动偏好维，每步重查并重跑 L2（§六）。"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from kms.query.l2 import L2Result
from kms.scope import (
    AUTHORIZATION_COLLECTION_DIMS,
    SET_DIMS,
    FilterAST,
    RequestMetrics,
    render_scope_sql,
)


@dataclass(frozen=True, slots=True)
class LadderDecision:
    action: str
    next_ast: FilterAST | None = None
    reason: str = ""


async def _count_chunks(
    connection: asyncpg.Connection, *, language: str, ast: FilterAST, dims: tuple[str, ...]
) -> int:
    predicate, params = render_scope_sql(
        ast,
        start_param=2,
        dims=dims,
        include_publish_state=True,
        include_validity=True,
        alias="cf",
    )
    sql = f"""
        SELECT count(*) FROM chunk_filter cf
        JOIN knowledge k ON k.kid = cf.kid AND k.current_version = cf.kid_version
        WHERE k.language = $1 AND k.publish_state = 'PUBLISHED' AND {predicate}
    """
    return int(await connection.fetchval(sql, language, *params))


async def classify_shortage(
    connection: asyncpg.Connection,
    *,
    language: str,
    ast: FilterAST,
    l2: L2Result,
    metrics: RequestMetrics,
    preference_dims: tuple[str, ...],
    rerank_enabled: bool,
) -> str:
    if any(verdict.auth_failed for verdict in l2.chunk_verdicts.values()):
        return "authorization"
    if not metrics.l1_before:
        # 归因证据缺失：按“不放宽”处理（§6.2），宁可空返回。
        return "attribution_missing"
    auth_count = await _count_chunks(
        connection, language=language, ast=ast, dims=AUTHORIZATION_COLLECTION_DIMS
    )
    if auth_count == 0:
        return "authorization"
    full_count = await _count_chunks(connection, language=language, ast=ast, dims=SET_DIMS)
    preference_available = any(dim not in ast.relaxed_dims for dim in preference_dims)
    if full_count == 0:
        return "preference" if preference_available else "authorization"
    if l2.entered.get("chunks", 0) == 0:
        return "score_threshold" if rerank_enabled else "budget"
    if metrics.l1_missing_chunk_ids > 0 and full_count < l2.answerable_count + 1:
        return "preference" if preference_available else "authorization"
    return "budget"


async def decide(
    connection: asyncpg.Connection,
    *,
    language: str,
    ast: FilterAST,
    l2: L2Result,
    metrics: RequestMetrics,
    min_chunks: int,
    preference_dims: tuple[str, ...],
    batch_retry_used: bool,
    params_retry_used: bool,
    rerank_enabled: bool = False,
) -> LadderDecision:
    if "chunks" in l2.aborted_channels:
        if not batch_retry_used:
            return LadderDecision(action="retry_batch", reason="l2_batch_aborted")
        return LadderDecision(action="abort_batch", reason="l2_batch_aborted")
    if l2.answerable_count >= min_chunks:
        return LadderDecision(action="answer")
    reason = await classify_shortage(
        connection,
        language=language,
        ast=ast,
        l2=l2,
        metrics=metrics,
        preference_dims=preference_dims,
        rerank_enabled=rerank_enabled,
    )
    metrics.shortage = reason
    if reason == "authorization":
        return LadderDecision(action="empty_auth", reason=reason)
    if reason == "preference":
        next_ast = ast.next_relaxation(preference_dims)
        if next_ast is None:
            return LadderDecision(action="exhausted", reason="ladder_exhausted")
        return LadderDecision(action="relax", next_ast=next_ast, reason=reason)
    if reason == "budget":
        if params_retry_used:
            return LadderDecision(action="exhausted", reason="budget_exhausted")
        return LadderDecision(action="retry_params", reason=reason)
    if reason in {"score_threshold", "attribution_missing"}:
        return LadderDecision(action="exhausted", reason=reason)
    return LadderDecision(action="exhausted", reason="unknown")
