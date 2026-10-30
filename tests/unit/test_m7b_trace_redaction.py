"""M7b：span 名称固定、属性白名单、Langfuse 元数据脱敏。"""

from __future__ import annotations

from src.kms.observability.langfuse import LangfuseObserver, Observation, sanitize_payload
from src.kms.observability.tracing import SPAN_NAMES, sanitize_attributes


def test_span_names_match_design() -> None:
    assert "rag-answer" in SPAN_NAMES
    assert "context-assembly" in SPAN_NAMES
    assert "citation-validation" in SPAN_NAMES


def test_span_attributes_drop_sensitive_fields() -> None:
    safe = sanitize_attributes(
        {
            "count": 3,
            "mode": "mix",
            "query": "用户原始提问",
            "content": "正文",
            "chunk_id": "c1",
            "prompt_version": "v1",
            "prompt_tokens": 12,
        }
    )
    assert safe["count"] == 3 and safe["mode"] == "mix"
    assert safe["prompt_version"] == "v1" and safe["prompt_tokens"] == 12
    assert "query" not in safe and "content" not in safe and "chunk_id" not in safe


def test_langfuse_observation_has_no_body() -> None:
    observation = Observation(
        model="local-llm",
        operation="answer-generation",
        prompt_version="v1",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=12.0,
        status="success",
    )
    observer = LangfuseObserver(enabled=False)
    payload = observer.log_observation(observation)
    assert "prompt" not in payload and "content" not in payload
    assert payload["model"] == "local-llm"
    assert sanitize_payload({"prompt": "x", "input": "y", "status": "ok"}) == {"status": "ok"}
