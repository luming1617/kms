"""评测 runner：真实检索结果进指标，不允许手写理想上下文。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval.gate import evaluate_gate
from eval.metrics import CaseScore, aggregate, score_case
from eval.models import GoldenCase
from src.kms.scope import SessionScope

AnswerCallable = Callable[[SessionScope, str], Awaitable[Any]]


def session_for_case(case: GoldenCase) -> SessionScope:
    scope = case.scope
    return SessionScope(
        language=case.language,
        product=tuple(scope["product"]),
        year=tuple(scope["year"]),
        security_level=scope["security_level"],
        asset=tuple(scope.get("asset", ())),
        region=tuple(scope.get("region", ())),
    )


async def run_cases(
    answer: AnswerCallable, cases: list[GoldenCase]
) -> tuple[dict[str, float], list[CaseScore]]:
    scores: list[CaseScore] = []
    for case in cases:
        result = await answer(session_for_case(case), case.question)
        scores.append(score_case(case, result))
    return aggregate(scores), scores


def write_report(
    path: Path,
    *,
    dataset_version: str,
    dataset_sha256: str,
    scores: dict[str, float],
    case_scores: list[CaseScore],
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    passed, gates = evaluate_gate(scores)
    report = {
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "environment": environment or {},
        "scores": scores,
        "gates": gates,
        "passed": passed,
        "cases": [asdict(score) for score in case_scores],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
