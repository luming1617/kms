"""模型轨：judge 解析、聚合与门禁。"""

from __future__ import annotations

from types import SimpleNamespace

from eval.gate import evaluate_model_gate
from eval.model_track import run_model_track
from eval.models import GoldenCase, SourceDocument


class FakeJudge:
    async def judge(self, _prompt: str) -> dict[str, float]:
        return {"faithfulness": 0.9, "answer_relevancy": 0.8, "context_relevancy": 0.7}


def case(answerable: bool = True) -> GoldenCase:
    return GoldenCase(
        case_id="c1",
        question_type="single_hop",
        question="q",
        reference_answer="r",
        answerable=answerable,
        language="zh-CN",
        scope={
            "product": ["SPOT"],
            "year": ["2024"],
            "security_level": "Secret",
            "asset": ["BTC"],
            "region": ["USA"],
        },
        source_documents=(SourceDocument("K", 1, "BIZ-K"),) if answerable else (),
    )


async def test_model_track_uses_retrieval_context() -> None:
    async def answer(_session, _question):
        return SimpleNamespace(
            answered=True,
            answer="回答 [1]",
            citations=[],
            retrieval_context=("正文 A",),
        )

    scores, details = await run_model_track(answer, [case()], FakeJudge())
    assert scores["faithfulness"] == 0.9
    assert scores["answer_relevancy"] == 0.8
    assert scores["context_relevancy"] == 0.7
    assert details[0].case_id == "c1"


async def test_unanswerable_refusal_scores_full() -> None:
    async def answer(_session, _question):
        return SimpleNamespace(answered=False, answer="暂无可答复内容", retrieval_context=())

    scores, _ = await run_model_track(answer, [case(answerable=False)], FakeJudge())
    assert scores["faithfulness"] == 1.0


def test_model_gate_thresholds() -> None:
    passed, results = evaluate_model_gate(
        {"faithfulness": 0.9, "answer_relevancy": 0.8, "context_relevancy": 0.8}
    )
    assert passed and all(results.values())
    failed, _ = evaluate_model_gate(
        {"faithfulness": 0.89, "answer_relevancy": 0.8, "context_relevancy": 0.8}
    )
    assert failed is False
