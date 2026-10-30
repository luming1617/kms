"""M0 退出证据：表名、签名、chunk_id 形态、探测报告齐全。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.m0


def test_real_table_names_within_identifier_limit() -> None:
    from probes.probe_tables import collect

    payload = collect()
    assert payload["passed"], payload
    assert payload["pg_max_identifier_length"] == 63
    assert payload["limit_violation_detected"] is True
    assert payload["model_name_missing_suffix"] is None


def test_nine_methods_and_ten_injection_points() -> None:
    from probes.probe_signatures import collect

    payload = collect()
    assert payload["passed"], payload
    assert len(payload["methods"]) == 9
    assert payload["injection_points"]["count"] == 10
    assert all(method["ok"] for method in payload["methods"])
    assert payload["backends"]["vector_signature_with_framework_name"]["enable_vector"] is True


def test_chunk_id_static_shape() -> None:
    from probes.probe_chunk_ids import static_contract

    payload = static_contract()
    assert payload["all_match"] and payload["positional"]


def test_m0_reports_exist(project_root: Path) -> None:
    report_dir = project_root / "reports" / "m0" / "final"
    for name in ("01-tables", "02-signatures", "03-chunk-ids", "04-index-cost", "05-load"):
        assert (report_dir / f"{name}.json").exists(), name
    assert (report_dir / "M0-验收报告.md").exists()
