"""M7：指标命名/标签白名单/丢弃维度聚合。"""

from __future__ import annotations

import pytest

from src.kms.observability.metrics import (
    _assert_labels,
    discard_dimensions,
    observe_http,
    render_metrics,
)
from src.kms.scope import DiscardRecord


def test_forbidden_labels_rejected() -> None:
    with pytest.raises(ValueError):
        _assert_labels({"user_id": "u1"})
    with pytest.raises(ValueError):
        _assert_labels({"trace_id": "t1"})
    with pytest.raises(ValueError):
        _assert_labels({"unknown": "x"})


def test_metrics_render_and_names() -> None:
    observe_http(route="/v1/answer", method="POST", status_code=200, duration_seconds=0.01)
    payload, content_type = render_metrics()
    text = payload.decode("utf-8")
    assert "knowledge_api_http_requests_total" in text
    assert "knowledge_api_http_request_duration_seconds_bucket" in text
    assert "text/plain" in content_type


def test_discard_dimensions_aggregation() -> None:
    records = [
        DiscardRecord("chunks", "c1", "k1", ("product",), "l2"),
        DiscardRecord("chunks", "c2", "k2", ("product",), "l2"),
        DiscardRecord("entities", None, None, ("missing_tracking",), "l2"),
    ]
    aggregated = discard_dimensions(records)
    assert ["chunks", "product", "l2", 2] in aggregated
    assert ["entities", "missing_tracking", "l2", 1] in aggregated
