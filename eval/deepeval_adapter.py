"""DeepEval / Ragas 可选适配；未安装时返回不可用，不阻塞快速门禁。"""

from __future__ import annotations


def available_metrics() -> dict[str, bool]:
    metrics = {}
    try:
        import deepeval  # noqa: F401

        metrics["deepeval"] = True
    except ImportError:
        metrics["deepeval"] = False
    try:
        import ragas  # noqa: F401

        metrics["ragas"] = True
    except ImportError:
        metrics["ragas"] = False
    return metrics


def require_metric_names() -> list[str]:
    return [
        "FaithfulnessMetric",
        "ContextualRelevancyMetric",
        "ContextualRecallMetric",
        "ContextualPrecisionMetric",
        "AnswerRelevancyMetric",
        "HallucinationMetric",
    ]
