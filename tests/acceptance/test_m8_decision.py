"""M8：决策记录必须给出做/不做与数据依据，不预设结论。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.m8


def test_decision_record_has_data_and_reasons(project_root: Path) -> None:
    report_dir = project_root / "reports" / "m8"
    decision = json.loads((report_dir / "decision.json").read_text(encoding="utf-8"))
    assert decision["partition_by_language"] in {"enable", "disable"}
    assert decision["self_assembly"] in {"enable", "disable"}
    assert decision["stronger_graph_prefilter"] in {"enable", "disable"}
    evidence = decision["evidence"]
    assert evidence["rows"] >= 10_000
    assert evidence["single"]["p95_ms"] > 0
    assert 0.0 <= evidence["single"]["recall_at_10"] <= 1.0
    assert all(decision["reasons"].values())
    assert (report_dir / "decision.md").exists()
    if decision["partition_by_language"] == "disable":
        assert evidence["partition"]["p95_ms"] >= evidence["single"]["p95_ms"] * 0.9 or (
            evidence["partition"]["recall_at_10"] < evidence["single"]["recall_at_10"] - 0.01
        )
