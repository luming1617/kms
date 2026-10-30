"""门禁阈值（§10.3 / §10.5）。"""

from __future__ import annotations

MODEL_THRESHOLDS = {
    "faithfulness": 0.90,
    "answer_relevancy": 0.80,
    "context_relevancy": 0.80,
}

THRESHOLDS = {
    "leakage_rate": 0.0,
    "faithfulness": 0.90,
    "context_recall": 0.80,
    "context_precision": 0.70,
    "prefix_clean": 1.0,
}


def evaluate_gate(scores: dict[str, float]) -> tuple[bool, dict[str, bool]]:
    results = {
        name: (
            scores.get(name, 0.0) <= threshold
            if name == "leakage_rate"
            else scores.get(name, 0.0) >= threshold
        )
        for name, threshold in THRESHOLDS.items()
    }
    return all(results.values()), results


def evaluate_model_gate(scores: dict[str, float]) -> tuple[bool, dict[str, bool]]:
    results = {
        name: scores.get(name, 0.0) >= threshold for name, threshold in MODEL_THRESHOLDS.items()
    }
    return all(results.values()), results
