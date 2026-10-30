"""请求侧 scope：唯一谓词渲染、contextvars 传递、写路径护栏与打点上下文。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from kms.config import SECURITY_LEVEL_READ_SETS

AUTHORIZATION_COLLECTION_DIMS: tuple[str, ...] = ("product", "year", "security_level")
PREFERENCE_DIMS: tuple[str, ...] = ("asset", "region")
SET_DIMS: tuple[str, ...] = AUTHORIZATION_COLLECTION_DIMS + PREFERENCE_DIMS
PUBLISH_STATE_DIM = "publish_state"
MAX_TOKEN_LENGTH = 128


class ScopeError(ValueError):
    """请求侧 scope 不合法；必须拒答，不得回退成全部可见。"""


class ScopeContaminationError(RuntimeError):
    """写路径检测到请求期 scope，必须当场失败（§4.4 铁律 1）。"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def _normalize(values: Iterable[str], dim: str, *, allow_empty: bool) -> tuple[str, ...]:
    normalized = sorted({value.strip() for value in values if value is not None})
    if not allow_empty and not normalized:
        raise ScopeError(f"{dim} 不得为空：请求侧空集 = 该维零授权（§4.3）")
    for value in normalized:
        if not value or len(value) > MAX_TOKEN_LENGTH or any(ch.isspace() for ch in value):
            raise ScopeError(f"{dim} 的取值不合法：{value!r}")
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class FilterAST:
    """六维 + 有效期；relaxed_dims 只允许偏好维（L3）。"""

    product: tuple[str, ...]
    year: tuple[str, ...]
    security_level: tuple[str, ...]
    asset: tuple[str, ...] = ()
    region: tuple[str, ...] = ()
    publish_state: str = "PUBLISHED"
    valid_at: datetime = field(default_factory=utcnow)
    relaxed_dims: frozenset[str] = frozenset()
    allow_empty_authorization: bool = False

    def __post_init__(self) -> None:
        if not self.allow_empty_authorization:
            for dim in AUTHORIZATION_COLLECTION_DIMS:
                if not getattr(self, dim):
                    raise ScopeError(f"{dim} 不得为空：授权维永不放宽")
        if self.publish_state != "PUBLISHED":
            raise ScopeError("查询期 publish_state 只能为 PUBLISHED（§4.3）")
        if not self.relaxed_dims <= set(PREFERENCE_DIMS):
            raise ScopeError("松弛只允许偏好维 asset / region（§6.3）")
        if self.valid_at.tzinfo is None:
            raise ScopeError("valid_at 必须带时区")

    def relax(self, dim: str) -> FilterAST:
        if dim not in PREFERENCE_DIMS:
            raise ScopeError(f"{dim} 不是可松弛维")
        return replace(self, relaxed_dims=self.relaxed_dims | {dim})

    def next_relaxation(self, order: tuple[str, ...]) -> FilterAST | None:
        for dim in order:
            if dim not in self.relaxed_dims:
                return self.relax(dim)
        return None

    def signature(self) -> str:
        """六维 + 松弛状态；不含有效期（有效期由 corpus_generation 兜住，§15.2）。"""
        canonical = {
            "product": list(self.product),
            "year": list(self.year),
            "security_level": list(self.security_level),
            "asset": list(self.asset),
            "region": list(self.region),
            "publish_state": self.publish_state,
            "relaxed": sorted(self.relaxed_dims),
        }
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionScope:
    """只从会话构造：提问文本里的任何维度都不参与授权（§9.4）。"""

    language: str
    product: Iterable[str]
    year: Iterable[str]
    security_level: str | Iterable[str]
    asset: Iterable[str] = ()
    region: Iterable[str] = ()

    def to_ast(
        self,
        *,
        valid_at: datetime | None = None,
        read_sets: Mapping[str, tuple[str, ...]] | None = None,
    ) -> FilterAST:
        read_sets = read_sets or SECURITY_LEVEL_READ_SETS
        raw_levels = (
            [self.security_level]
            if isinstance(self.security_level, str)
            else list(self.security_level)
        )
        expanded: list[str] = []
        for level in raw_levels:
            if level not in read_sets:
                raise ScopeError(f"未知的 security_level：{level!r}")
            expanded.extend(read_sets[level])
        return FilterAST(
            product=_normalize(self.product, "product", allow_empty=True),
            year=_normalize(self.year, "year", allow_empty=True),
            security_level=_normalize(expanded, "security_level", allow_empty=True),
            asset=_normalize(self.asset, "asset", allow_empty=True),
            region=_normalize(self.region, "region", allow_empty=True),
            valid_at=valid_at or utcnow(),
            allow_empty_authorization=True,
        )


def _column(dim: str, alias: str | None) -> str:
    return f"{alias}.{dim}" if alias else dim


def render_scope_sql(
    ast: FilterAST,
    *,
    start_param: int,
    dims: tuple[str, ...] = SET_DIMS,
    include_publish_state: bool = True,
    include_validity: bool = True,
    alias: str | None = None,
) -> tuple[str, list[Any]]:
    """唯一一套渲染代码（铁律 3）：L1a/L1b/L2/阶梯共用。

    集合维固定形态：``(col = '{}'::text[] OR col && $n::text[])``。
    所有取值走 $n 参数；占位编号从 start_param 起连续分配。
    """
    clauses: list[str] = []
    params: list[Any] = []
    index = start_param
    for dim in dims:
        if dim in ast.relaxed_dims:
            continue
        col = _column(dim, alias)
        clauses.append(f"({col} = '{{}}'::text[] OR {col} && ${index}::text[])")
        params.append(list(getattr(ast, dim)))
        index += 1
    if include_publish_state:
        clauses.append(f"{_column(PUBLISH_STATE_DIM, alias)} = ${index}")
        params.append(ast.publish_state)
        index += 1
    if include_validity:
        valid_from = _column("valid_from", alias)
        valid_to = _column("valid_to", alias)
        clauses.append(
            f"({valid_from} <= ${index} AND ({valid_to} IS NULL OR {valid_to} > ${index}))"
        )
        params.append(ast.valid_at)
        index += 1
    return " AND ".join(clauses), params


scope_var: ContextVar[FilterAST | None] = ContextVar("ics_rag_scope", default=None)


class ScopeContext:
    """请求期 scope 的唯一入口；必须与 finally 中的 reset 成对（§9.4）。"""

    def __init__(self, ast: FilterAST) -> None:
        self._ast = ast
        self._token: Token[FilterAST | None] | None = None

    def __enter__(self) -> FilterAST:
        self._token = scope_var.set(self._ast)
        return self._ast

    def __exit__(self, *_exc: object) -> None:
        if self._token is not None:
            scope_var.reset(self._token)
            self._token = None


def set_scope(ast: FilterAST) -> Token[FilterAST | None]:
    return scope_var.set(ast)


def reset_scope(token: Token[FilterAST | None]) -> None:
    scope_var.reset(token)


def current_scope() -> FilterAST | None:
    return scope_var.get()


def clear_scope_for_background() -> None:
    """后台任务启动前显式清空；create_task 会复制当前 context（§4.4）。"""
    scope_var.set(None)


def ensure_write_path() -> None:
    if scope_var.get() is not None:
        raise ScopeContaminationError(
            "写路径检测到请求期 scope：写/删/重建必须显式清空 scope（§4.4 铁律 1）"
        )


AUTHORIZATION_FAILURE_DIMS = frozenset(
    set(AUTHORIZATION_COLLECTION_DIMS)
    | {"publish_state", "validity", "missing_snapshot", "missing_projection", "version_lag"}
)


@dataclass(slots=True)
class DiscardRecord:
    channel: str
    chunk_id: str | None
    kid: str | None
    failed_dims: tuple[str, ...]
    reason: str


@dataclass
class RequestMetrics:
    """请求级打点；只挂 contextvar，不跨请求共享。"""

    l1_predicate: bool = False
    l1_rows_before: int = 0
    l1_rows_after: int = 0
    l1_requested_chunk_ids: int = 0
    l1_missing_chunk_ids: int = 0
    l2_entered: dict[str, int] = field(default_factory=dict)
    l2_discarded: dict[str, int] = field(default_factory=dict)
    discards: list[DiscardRecord] = field(default_factory=list)
    shortage: str | None = None
    ladder_steps: int = 0
    relaxed_dims: tuple[str, ...] = ()
    batch_aborted: bool = False
    batch_aborted_channels: tuple[str, ...] = ()
    cache_hit: bool = False
    l1_before: dict[str, int] = field(default_factory=dict)
    l1_after: dict[str, int] = field(default_factory=dict)
    l1_pending_dropped: int = 0
    trace_id: str | None = None

    def note_l1(self, channel: str, before: int, after: int) -> None:
        self.l1_predicate = True
        self.l1_before[channel] = self.l1_before.get(channel, 0) + max(0, int(before))
        self.l1_after[channel] = self.l1_after.get(channel, 0) + max(0, int(after))

    def l1_cut(self, channel: str) -> int:
        return max(0, self.l1_before.get(channel, 0) - self.l1_after.get(channel, 0))

    def enter(self, channel: str, count: int) -> None:
        self.l2_entered[channel] = self.l2_entered.get(channel, 0) + count

    def discard(self, record: DiscardRecord) -> None:
        self.l2_discarded[record.channel] = self.l2_discarded.get(record.channel, 0) + 1
        self.discards.append(record)

    def discard_ratio(self, channel: str) -> float:
        entered = self.l2_entered.get(channel, 0)
        if entered == 0:
            return 0.0
        return self.l2_discarded.get(channel, 0) / entered


metrics_var: ContextVar[RequestMetrics | None] = ContextVar("ics_request_metrics", default=None)


def start_request_metrics() -> tuple[RequestMetrics, Token[RequestMetrics | None]]:
    metrics = RequestMetrics()
    return metrics, metrics_var.set(metrics)


def reset_request_metrics(token: Token[RequestMetrics | None]) -> None:
    metrics_var.reset(token)


def current_metrics() -> RequestMetrics | None:
    return metrics_var.get()
