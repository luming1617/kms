"""M7a：锁定集冻结、分层覆盖、确定性指标与门禁。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.dataset import file_sha256, load_cases, validate_cases
from eval.gate import evaluate_gate
from eval.metrics import aggregate, score_case
from eval.models import GoldenCase, SourceDocument
from eval.runner import run_cases
from src.kms.query.generation import Citation

pytestmark = pytest.mark.m7a


def locked(project_root: Path):
    path = project_root / "eval" / "golden" / "locked.jsonl"
    return path, load_cases(path)


def test_locked_set_frozen_and_layered(project_root: Path) -> None:
    path, cases = locked(project_root)
    assert 100 <= len(cases) <= 300
    assert not validate_cases(cases)
    expected_sha = (project_root / "eval" / "golden" / "locked.sha256").read_text().strip()
    assert file_sha256(path) == expected_sha
    assert {case.language for case in cases} == {"zh-CN", "en-US", "ja-JP"}
    assert {case.scope["year"][0] for case in cases} >= {"2024", "2025", "2026"}
    levels = {
        level
        for case in cases
        for level in (
            case.scope["security_level"]
            if isinstance(case.scope["security_level"], list)
            else [case.scope["security_level"]]
        )
    }
    assert levels >= {"TopSecret", "Confidential", "Secret", "Public"}
    assert any(not case.answerable for case in cases)
    assert any(case.question_type == "multi_hop" for case in cases)


def test_metrics_leakage_and_gate() -> None:
    case = GoldenCase(
        case_id="x",
        question_type="unanswerable",
        question="q",
        reference_answer="r",
        answerable=False,
        language="zh-CN",
        scope={
            "product": ["SPOT"],
            "year": ["2024"],
            "security_level": "Secret",
            "asset": ["BTC"],
            "region": ["USA"],
        },
    )
    leaked = SimpleNamespace(
        answered=True,
        answer="答案 [1]",
        citations=[Citation(index=1, biz_key="BIZ-X", chunk_id="c1")],
    )
    score = score_case(case, leaked)
    assert score.leakage == 1
    passed, _ = evaluate_gate(aggregate([score]))
    assert passed is False

    refused = SimpleNamespace(answered=False, answer="暂无可答复内容", citations=[])
    assert score_case(case, refused).leakage == 0

    answerable = GoldenCase(
        case_id="y",
        question_type="single_hop",
        question="q",
        reference_answer="r",
        answerable=True,
        language="zh-CN",
        scope=case.scope,
        source_documents=(SourceDocument("K", 1, "BIZ-K"),),
    )
    good = SimpleNamespace(
        answered=True,
        answer="答案 [1]",
        citations=[Citation(index=1, biz_key="BIZ-K", chunk_id="c1")],
    )
    good_score = score_case(answerable, good)
    assert good_score.faithfulness == 1 and good_score.context_recall == 1
    assert good_score.context_precision == 1
    assert good_score.leakage == 0 and good_score.legal_empty == 0

    leaked_citation = SimpleNamespace(
        answered=True,
        answer="答案 [1]",
        citations=[Citation(index=1, biz_key="BIZ-OTHER", chunk_id="c1")],
        retrieval_context=("[KNOWLEDGE:K] 正文",),
    )
    leaked_score = score_case(answerable, leaked_citation)
    assert leaked_score.leakage == 1
    assert leaked_score.prefix_clean == 0

    legal = SimpleNamespace(answered=False, shortage="authorization", retrieval_context=())
    legal_score = score_case(answerable, legal)
    assert legal_score.legal_empty == 1


async def test_runner_is_deterministic_for_fixed_cases() -> None:
    case = GoldenCase(
        case_id="z",
        question_type="single_hop",
        question="q",
        reference_answer="r",
        answerable=True,
        language="zh-CN",
        scope={
            "product": ["SPOT"],
            "year": ["2024"],
            "security_level": "Secret",
            "asset": ["BTC"],
            "region": ["USA"],
        },
        source_documents=(SourceDocument("K", 1, "BIZ-K"),),
    )

    async def fixed_answer(_session, _question):
        return SimpleNamespace(
            answered=True,
            answer="答案 [1]",
            citations=[Citation(index=1, biz_key="BIZ-K", chunk_id="c1")],
        )

    first, _ = await run_cases(fixed_answer, [case])
    second, _ = await run_cases(fixed_answer, [case])
    assert first == second
    assert first["leakage_rate"] == 0.0 and first["faithfulness"] == 1.0
