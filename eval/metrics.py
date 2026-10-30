"""确定性指标：忠实度、上下文召回/精确、越权泄漏、前缀清洗率。"""

from __future__ import annotations

from dataclasses import dataclass

from eval.models import GoldenCase


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    leakage: int
    faithfulness: float
    context_recall: float
    context_precision: float
    prefix_clean: float
    legal_empty: int
    cited_biz_keys: tuple[str, ...]


def score_case(case: GoldenCase, result: object) -> CaseScore:
    answered = bool(getattr(result, "answered", False))
    answer = str(getattr(result, "answer", ""))
    citations = getattr(result, "citations", []) or []
    cited = tuple(sorted({str(citation.biz_key) for citation in citations}))
    allowed = case.allowed_biz_keys

    out_of_scope = bool(set(cited) - allowed)
    leakage = 1 if answered and (not case.answerable or out_of_scope) else 0
    contexts = tuple(getattr(result, "retrieval_context", ()) or ())
    prefix_clean = (
        0.0
        if "[KNOWLEDGE:" in answer or any("[KNOWLEDGE:" in str(item) for item in contexts)
        else 1.0
    )
    legal_empty = (
        1
        if not answered and getattr(result, "shortage", None) in {"authorization", "validity"}
        else 0
    )
    faithfulness = (1.0 if cited and set(cited) <= allowed else 0.0) if answered else 1.0
    if case.answerable:
        context_recall = 1.0 if answered and allowed & set(cited) else 0.0
        context_precision = 1.0 if cited and set(cited) <= allowed else 0.0
    else:
        context_recall = 1.0 if not answered else 0.0
        context_precision = 1.0
    return CaseScore(
        case_id=case.case_id,
        leakage=leakage,
        faithfulness=faithfulness,
        context_recall=context_recall,
        context_precision=context_precision,
        prefix_clean=prefix_clean,
        legal_empty=legal_empty,
        cited_biz_keys=cited,
    )


def aggregate(scores: list[CaseScore]) -> dict[str, float]:
    if not scores:
        return {
            "case_count": 0.0,
            "leakage_rate": 0.0,
            "faithfulness": 0.0,
            "context_recall": 0.0,
            "context_precision": 0.0,
            "prefix_clean": 1.0,
            "legal_empty_count": 0.0,
            "legal_empty_rate": 0.0,
        }
    total = len(scores)
    return {
        "case_count": float(total),
        "leakage_rate": sum(score.leakage for score in scores) / total,
        "faithfulness": sum(score.faithfulness for score in scores) / total,
        "context_recall": sum(score.context_recall for score in scores) / total,
        "context_precision": sum(score.context_precision for score in scores) / total,
        "prefix_clean": sum(score.prefix_clean for score in scores) / total,
        "legal_empty_count": float(sum(score.legal_empty for score in scores)),
        "legal_empty_rate": sum(score.legal_empty for score in scores) / total,
    }
