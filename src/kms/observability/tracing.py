"""OTel Trace：固定 span 层级；属性白名单与正文禁入（§11.4 / §11.6）。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult
    from opentelemetry.trace import Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - 可选依赖
    _OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment, misc]
    TracerProvider = None  # type: ignore[assignment, misc]
    Resource = None  # type: ignore[assignment, misc]
    BatchSpanProcessor = None  # type: ignore[assignment, misc]
    Sampler = object  # type: ignore[assignment,misc]
    SamplingResult = None  # type: ignore[assignment, misc]
    Decision = None  # type: ignore[assignment, misc]
    Status = StatusCode = None  # type: ignore[assignment, misc]

SPAN_NAMES = (
    "rag-answer",
    "retrieval",
    "vector-search",
    "keyword-search",
    "reranking",
    "context-assembly",
    "answer-generation",
    "citation-validation",
    "final-response",
)

ALLOWED_ATTRIBUTES = frozenset(
    {
        "route",
        "mode",
        "channel",
        "count",
        "threshold",
        "cache_hit",
        "ladder_steps",
        "status",
        "duration_ms",
        "candidate_width",
        "iterative_scan",
        "result_count",
        "input_count",
        "output_count",
        "discarded",
        "relaxed_dims",
        "shortage",
        "model",
        "prompt_version",
        "prompt_tokens",
        "completion_tokens",
        "citation_count",
        "predicate_present",
        "workspace",
        "span.kind",
    }
)
FORBIDDEN_ATTRIBUTE_MARKERS = (
    "content",
    "query_text",
    "chunk_text",
    "answer_text",
    "user_id",
    "session_id",
    "chunk_id",
    "document_id",
    "authorization",
)

_configuration: dict[str, Any] = {"provider": None, "tracing": None}


class ErrorPrioritySampler(Sampler):  # type: ignore[misc]
    """错误一律保留；其余按 parent/ratio 采样。"""

    def __init__(self, ratio: float = 0.1) -> None:
        self._ratio = ratio
        self._counter = 0

    def get_description(self) -> str:
        return f"ErrorPrioritySampler(ratio={self._ratio})"

    def should_sample(  # type: ignore[override]
        self,
        parent_context: Any = None,
        trace_id: int = 0,
        name: str = "",
        kind: Any = None,
        attributes: dict[str, Any] | None = None,
        links: Any = None,
        trace_state: Any = None,
    ) -> Any:
        # OTel SDK 以位置参数调用；只读 kwargs 会让 trace_id 恒为 None。
        if attributes and attributes.get("error") is True:
            return SamplingResult(  # type: ignore[misc]
                decision=Decision.RECORD_AND_SAMPLE,
                attributes=None,
                trace_state=trace_state,
            )
        self._counter += 1
        decision = (
            Decision.RECORD_AND_SAMPLE
            if (trace_id % 100) < int(self._ratio * 100)
            else Decision.DROP
        )
        return SamplingResult(  # type: ignore[misc]
            decision=decision, attributes=None, trace_state=trace_state
        )


def current_trace_id() -> str | None:
    if not _OTEL_AVAILABLE:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


def configure_tracing(*, service_name: str = "knowledge-api", endpoint: str | None = None) -> Any:
    if not _OTEL_AVAILABLE:
        return None
    if _configuration["provider"] is not None:
        return _configuration["tracing"]
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=ErrorPrioritySampler(),
    )
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configuration["provider"] = provider
    _configuration["tracing"] = trace.get_tracer("kms")
    return _configuration["tracing"]


def set_tracer(tracer_instance: Any) -> None:
    """测试/嵌入场景注入 tracer；生产由 configure_tracing 设置。"""
    _configuration["tracing"] = tracer_instance


def tracer() -> Any:
    if not _OTEL_AVAILABLE:
        return None
    return _configuration["tracing"] or trace.get_tracer("kms")


def sanitize_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    if not attributes:
        return {}
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        lowered = key.lower()
        if key not in ALLOWED_ATTRIBUTES:
            continue
        if any(marker in lowered for marker in FORBIDDEN_ATTRIBUTE_MARKERS):
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
    return safe


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    if not _OTEL_AVAILABLE:
        yield None
        return
    tracer_instance = tracer()
    with tracer_instance.start_as_current_span(name) as current:
        for key, value in sanitize_attributes(attributes).items():
            current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            # 只记异常类型：异常消息可能夹带 prompt/正文（§11.6）。
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def mark_error(current: Any, error: Exception) -> None:
    if current is not None and _OTEL_AVAILABLE:
        current.set_status(Status(StatusCode.ERROR, type(error).__name__))
