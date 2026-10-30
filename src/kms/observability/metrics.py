"""Prometheus 指标：三层命名与标签白名单（§11.3 / §12.1）。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Gauge,
    Histogram,
    generate_latest,
    push_to_gateway,
)
from prometheus_client import Counter as PCounter

SERVICE = "knowledge-api"
LABEL_WHITELIST = frozenset(
    {
        "service",
        "route",
        "method",
        "status_code",
        "dependency",
        "provider",
        "model",
        "operation",
        "status",
        "workspace",
        "channel",
        "dim",
        "reason",
        "mode",
    }
)
FORBIDDEN_LABELS = frozenset(
    {
        "user_id",
        "session_id",
        "request_id",
        "trace_id",
        "query",
        "document_id",
        "chunk_id",
        "prompt",
        "content",
        "answer",
    }
)

HTTP_REQUESTS = PCounter(
    "knowledge_api_http_requests_total",
    "HTTP requests",
    ["service", "route", "method", "status_code"],
)
HTTP_DURATION = Histogram(
    "knowledge_api_http_request_duration_seconds", "HTTP latency", ["service", "route", "method"]
)
MODEL_TOKENS = PCounter(
    "knowledge_api_model_tokens_total", "Model tokens", ["provider", "model", "operation", "status"]
)
DB_POOL_WAIT = Histogram("knowledge_api_db_pool_wait_seconds", "DB pool wait", ["service"])
LLM_REQUESTS = PCounter(
    "llm_requests_total", "LLM requests", ["provider", "model", "operation", "status"]
)
LLM_DURATION = Histogram(
    "llm_request_duration_seconds", "LLM latency", ["provider", "model", "operation"]
)
LLM_TOKENS = PCounter("llm_tokens_total", "LLM tokens", ["provider", "model", "operation"])
RAG_DURATION = Histogram("rag_retrieval_duration_seconds", "RAG retrieval latency", ["mode"])
RAG_RESULTS = Histogram("rag_retrieval_results", "RAG result count", ["mode"])
L1_DISCARD = PCounter(
    "knowledge_l1_candidate_cut_total", "L1 candidate cut", ["workspace", "channel"]
)
L2_ENTERED = PCounter("knowledge_l2_channel_entered_total", "L2 entered", ["workspace", "channel"])
L2_DISCARD = PCounter(
    "knowledge_l2_channel_discard_total",
    "L2 discard by channel/dim",
    ["workspace", "channel", "dim", "reason"],
)
SHORTAGE = PCounter("knowledge_shortage_total", "Shortage attribution", ["workspace", "reason"])
LADDER_STEPS = Histogram("knowledge_ladder_steps", "L3 ladder steps", ["workspace"])
LADDER_RELAXED = PCounter(
    "knowledge_ladder_relaxed_total", "L3 relaxed dimensions", ["workspace", "dim"]
)
L1_PENDING_DROPPED = PCounter(
    "knowledge_l1_pending_dropped_total",
    "Pending vector ids dropped fail-closed",
    ["workspace"],
)
CACHE_HIT = PCounter("knowledge_cache_hit_total", "Answer cache hits", ["workspace"])
L1_BEFORE = PCounter(
    "knowledge_l1_candidates_before_total",
    "L1 candidate window before predicate",
    ["workspace", "channel"],
)
L1_AFTER = PCounter(
    "knowledge_l1_candidates_after_total",
    "L1 candidates after predicate",
    ["workspace", "channel"],
)
L2_BATCH_ABORTED = PCounter(
    "knowledge_l2_batch_aborted_total", "L2 batch safety aborts", ["workspace", "channel"]
)
LEAKAGE = PCounter(
    "knowledge_leakage_total",
    "L2 authorization-dimension discards (potential L1 leak)",
    ["workspace", "channel", "dim"],
)
GATEWAY_REJECTIONS = PCounter("knowledge_gateway_rejection_total", "Gateway rejections", ["reason"])
RECONCILE_LAST_SUCCESS = Gauge(
    "knowledge_reconcile_last_success_timestamp_seconds", "Last successful nightly reconcile"
)
PUBLISH_QUEUE_DEPTH = Gauge("knowledge_publish_queue_depth", "Pending conflict review queue depth")
EVAL_FAITHFULNESS = Gauge("knowledge_eval_faithfulness", "Latest offline model-track faithfulness")
CACHE_ENTRIES = Gauge("knowledge_answer_cache_entries", "Answer cache entries")


def _assert_labels(labels: dict[str, str]) -> None:
    forbidden = FORBIDDEN_LABELS & set(labels)
    if forbidden:
        raise ValueError(f"禁止的指标标签：{sorted(forbidden)}")
    unknown = set(labels) - LABEL_WHITELIST
    if unknown:
        raise ValueError(f"未列入白名单的指标标签：{sorted(unknown)}")


def observe_http(*, route: str, method: str, status_code: int, duration_seconds: float) -> None:
    labels = {"service": SERVICE, "route": route, "method": method, "status_code": str(status_code)}
    _assert_labels(labels)
    HTTP_REQUESTS.labels(**labels).inc()
    HTTP_DURATION.labels(service=SERVICE, route=route, method=method).observe(duration_seconds)


def observe_llm(
    *,
    provider: str,
    model: str,
    operation: str,
    status: str,
    duration_seconds: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    labels = {"provider": provider, "model": model, "operation": operation, "status": status}
    _assert_labels(labels)
    LLM_REQUESTS.labels(**labels).inc()
    LLM_DURATION.labels(provider=provider, model=model, operation=operation).observe(
        duration_seconds
    )
    if prompt_tokens:
        LLM_TOKENS.labels(provider=provider, model=model, operation=operation).inc(prompt_tokens)
    if completion_tokens:
        LLM_TOKENS.labels(provider=provider, model=model, operation=operation).inc(
            completion_tokens
        )
    MODEL_TOKENS.labels(provider=provider, model=model, operation=operation, status=status).inc(
        prompt_tokens + completion_tokens
    )


def observe_db_pool_wait(duration_seconds: float) -> None:
    DB_POOL_WAIT.labels(service=SERVICE).observe(duration_seconds)


def observe_retrieval(*, mode: str, duration_seconds: float, result_count: int) -> None:
    _assert_labels({"mode": mode})
    RAG_DURATION.labels(mode=mode).observe(duration_seconds)
    RAG_RESULTS.labels(mode=mode).observe(result_count)


_AUTH_FAILURE_DIMS = frozenset(
    {
        "product",
        "year",
        "security_level",
        "publish_state",
        "validity",
        "missing_snapshot",
        "missing_projection",
        "version_lag",
    }
)


def record_query_result(workspace: str, result: Any) -> None:
    metrics = getattr(result, "metrics", {}) or {}
    entered = metrics.get("l2_entered", {})
    for channel, count in entered.items():
        if count:
            L2_ENTERED.labels(workspace=workspace, channel=channel).inc(count)
    before = metrics.get("l1_before", {})
    after = metrics.get("l1_after", {})
    for channel, count in before.items():
        L1_BEFORE.labels(workspace=workspace, channel=channel).inc(int(count))
    for channel, count in after.items():
        L1_AFTER.labels(workspace=workspace, channel=channel).inc(int(count))
    if metrics.get("l1_missing_chunk_ids"):
        L1_DISCARD.labels(workspace=workspace, channel="text_chunks").inc(
            int(metrics["l1_missing_chunk_ids"])
        )
    for item in metrics.get("discard_dimensions", []):
        channel, dim, reason, count = item
        _assert_labels({"workspace": workspace, "channel": channel, "dim": dim, "reason": reason})
        L2_DISCARD.labels(workspace=workspace, channel=channel, dim=dim, reason=reason).inc(count)
        if dim in _AUTH_FAILURE_DIMS:
            LEAKAGE.labels(workspace=workspace, channel=channel, dim=dim).inc(count)
    if metrics.get("l1_pending_dropped"):
        L1_PENDING_DROPPED.labels(workspace=workspace).inc(int(metrics["l1_pending_dropped"]))
    if metrics.get("batch_aborted"):
        for channel in metrics.get("batch_aborted_channels", ("chunks",)):
            L2_BATCH_ABORTED.labels(workspace=workspace, channel=channel).inc()
    if getattr(result, "shortage", None):
        SHORTAGE.labels(workspace=workspace, reason=result.shortage).inc()
    LADDER_STEPS.labels(workspace=workspace).observe(getattr(result, "ladder_steps", 0))
    if getattr(result, "cache_hit", False):
        CACHE_HIT.labels(workspace=workspace).inc()


def discard_dimensions(records: list[Any]) -> list[list[Any]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for record in records:
        dims = record.failed_dims or ("unknown",)
        for dim in dims:
            counts[(record.channel, dim, record.reason)] += 1
    return [[channel, dim, reason, count] for (channel, dim, reason), count in counts.items()]


def observe_gateway_rejection(*, reason: str) -> None:
    _assert_labels({"reason": reason})
    GATEWAY_REJECTIONS.labels(reason=reason).inc()


def push_metrics(gateway: str, *, job: str) -> None:
    push_to_gateway(gateway.rstrip("/"), job=job, registry=REGISTRY)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
