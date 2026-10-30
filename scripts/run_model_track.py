"""跑模型轨（LLM-as-judge）；结果写报告并推送到 Pushgateway（如配置）。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eval.dataset import file_sha256, load_cases
from eval.gate import evaluate_model_gate
from eval.model_track import LlmJudge, run_model_track
from src.kms.config import load_settings
from src.kms.observability.metrics import EVAL_FAITHFULNESS, push_metrics
from src.kms.providers import OpenAICompatibleEmbeddingProvider, OpenAICompatibleLLM
from src.kms.runtime import build_runtime
from src.kms.scope import SessionScope

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    settings = load_settings()
    if not settings.llm.base_url:
        raise SystemExit("ICS_LLM_BASE_URL 未配置")
    embedding = OpenAICompatibleEmbeddingProvider(
        base_url=settings.embedding_base_url or settings.llm.base_url,
        api_key=settings.embedding_api_key or settings.llm.api_key,
        timeout_seconds=settings.embedding_timeout_seconds,
    )
    llm = OpenAICompatibleLLM(
        base_url=settings.llm.base_url,
        model=settings.llm.model_id,
        api_key=settings.llm.api_key,
        timeout_seconds=settings.llm.timeout_seconds,
    )
    runtime = await build_runtime(settings, embedding_provider=embedding, llm=llm)
    records = await runtime.registry.list_all()
    environment = {
        record.lang: {
            "corpus_generation": record.corpus_generation,
            "embedding_model": record.embedding_model,
            "embedding_dim": record.embedding_dim,
        }
        for record in records
    }
    cases = load_cases(ROOT / "eval" / "golden" / "locked.jsonl")

    async def answer(session: SessionScope, question: str):
        service = runtime.query_services.get(session.language)
        if service is None:
            raise RuntimeError(f"语言未装配：{session.language}")
        return await service.answer(session, question)

    try:
        scores, case_scores = await run_model_track(answer, cases, LlmJudge(llm))
    finally:
        await runtime.aclose()
    passed, gates = evaluate_model_gate(scores)
    report = {
        "dataset_sha256": file_sha256(ROOT / "eval" / "golden" / "locked.jsonl"),
        "environment": environment,
        "model": settings.llm.model_id,
        "judge_model": settings.llm.model_id,
        "prompt_version": settings.llm.prompt_version,
        "scores": scores,
        "gates": gates,
        "passed": passed,
        "cases": [asdict(item) for item in case_scores],
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "reports" / "m7a" / f"model-track-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    EVAL_FAITHFULNESS.set(float(scores["faithfulness"]))
    if settings.pushgateway_url:
        push_metrics(settings.pushgateway_url, job="kms-model-track")
    print(json.dumps({"scores": scores, "passed": passed}, ensure_ascii=False))
    print(f"report: {out}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
