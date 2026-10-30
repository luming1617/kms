"""模型轨：LLM-as-judge 的忠实度 / 答案相关性 / 上下文相关性。"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from eval.models import GoldenCase
from eval.runner import session_for_case
from kms.scope import SessionScope

AnswerCallable = Callable[[SessionScope, str], Awaitable[Any]]


class ModelTrackError(RuntimeError):
    pass


class Judge(Protocol):
    async def judge(self, prompt: str) -> dict[str, float]: ...


@dataclass(frozen=True, slots=True)
class ModelTrackScore:
    case_id: str
    faithfulness: float
    answer_relevancy: float
    context_relevancy: float


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ModelTrackError("judge did not return JSON")
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ModelTrackError("judge JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ModelTrackError("judge JSON is not an object")
    return payload


class LlmJudge:
    def __init__(self, llm: Callable[[str], Awaitable[str]]) -> None:
        self._llm = llm

    async def judge(self, prompt: str) -> dict[str, float]:
        raw = await self._llm(prompt)
        payload = _extract_json(raw)
        result: dict[str, float] = {}
        for key in ("faithfulness", "answer_relevancy", "context_relevancy"):
            value = payload.get(key)
            if not isinstance(value, (int, float)):
                raise ModelTrackError(f"judge missing numeric {key}")
            result[key] = max(0.0, min(1.0, float(value)))
        return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


async def score_model_case(case: GoldenCase, result: Any, judge: Judge) -> ModelTrackScore:
    answered = bool(getattr(result, "answered", False))
    if not case.answerable and not answered:
        return ModelTrackScore(case.case_id, 1.0, 1.0, 1.0)
    contexts = list(getattr(result, "retrieval_context", ()) or ())
    prompt = (
        "你是严格的 RAG 评测员。只输出 JSON，不要解释。\n"
        f"问题：{case.question}\n"
        f"参考答案：{case.reference_answer}\n"
        f"模型回答：{getattr(result, 'answer', '')}\n"
        "检索上下文：\n"
        + "\n---\n".join(contexts[:20])
        + "\n\n请按 0~1 打分：faithfulness（回答是否被上下文支持）、"
        "answer_relevancy（回答是否切题）、context_relevancy（上下文是否与问题相关）。"
        '输出格式：{"faithfulness": 0.0, "answer_relevancy": 0.0, "context_relevancy": 0.0}'
    )
    scores = await judge.judge(prompt)
    return ModelTrackScore(
        case_id=case.case_id,
        faithfulness=_clamp(scores["faithfulness"]),
        answer_relevancy=_clamp(scores["answer_relevancy"]),
        context_relevancy=_clamp(scores["context_relevancy"]),
    )


async def run_model_track(
    answer: AnswerCallable, cases: list[GoldenCase], judge: Judge
) -> tuple[dict[str, float], list[ModelTrackScore]]:
    scores: list[ModelTrackScore] = []
    for case in cases:
        result = await answer(session_for_case(case), case.question)
        scores.append(await score_model_case(case, result, judge))
    if not scores:
        return {
            "case_count": 0.0,
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_relevancy": 0.0,
        }, []
    total = len(scores)
    return {
        "case_count": float(total),
        "faithfulness": sum(item.faithfulness for item in scores) / total,
        "answer_relevancy": sum(item.answer_relevancy for item in scores) / total,
        "context_relevancy": sum(item.context_relevancy for item in scores) / total,
    }, scores
