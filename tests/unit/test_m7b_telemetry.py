"""M7b：真实 OTel span 层级、错误状态、告警与采集配置。"""

from __future__ import annotations

from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from scripts.alert_drill import run_drills
from src.kms.observability import tracing

pytestmark = pytest.mark.m7b


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    trace.set_tracer_provider(provider)
    tracing.set_tracer(provider.get_tracer("test"))
    return memory


def test_span_hierarchy_and_redaction(exporter: InMemorySpanExporter) -> None:
    with (
        tracing.span("retrieval", {"count": 3, "content": "正文", "query": "原始提问"}),
        tracing.span("vector-search", {"mode": "mix", "chunk_id": "c1"}),
    ):
        pass
    spans = exporter.get_finished_spans()
    names = [span.name for span in spans]
    assert "retrieval" in names and "vector-search" in names
    retrieval_span = next(span for span in spans if span.name == "retrieval")
    assert retrieval_span.attributes["count"] == 3
    assert "content" not in retrieval_span.attributes
    assert "query" not in retrieval_span.attributes
    vector_span = next(span for span in spans if span.name == "vector-search")
    assert "chunk_id" not in vector_span.attributes


def test_error_span_status(exporter: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError), tracing.span("answer-generation", {"count": 1}):
        raise RuntimeError("llm down")
    span = exporter.get_finished_spans()[-1]
    assert span.status.status_code.name == "ERROR"


def test_alerts_and_collector_config(project_root: Path) -> None:
    alerts = (project_root / "docker" / "alerts.yml").read_text(encoding="utf-8")
    for rule in ("KnowledgeApiHigh5xx", "NightlyReconcileMissing", "LeakageNonZero"):
        assert rule in alerts
    collector = (project_root / "docker" / "otel-collector-config.yml").read_text(encoding="utf-8")
    for token in ("otlp", "4317", "4318", "attributes/redact", "memory_limiter"):
        assert token in collector
    assert all(drill["fired"] for drill in run_drills())
