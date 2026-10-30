"""确定性评测：真实 QueryService + 锁定集，写报告（含环境代次/模型）。"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from eval.dataset import file_sha256, load_cases
from eval.runner import run_cases, write_report
from src.kms.config import load_settings
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
        scores, case_scores = await run_cases(answer, cases)
    finally:
        await runtime.aclose()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "reports" / "m7a" / f"deterministic-{stamp}.json"
    report = write_report(
        out,
        dataset_version="golden-v1",
        dataset_sha256=file_sha256(ROOT / "eval" / "golden" / "locked.jsonl"),
        scores=scores,
        case_scores=case_scores,
        environment=environment,
    )
    print(json.dumps({"scores": scores, "passed": report["passed"]}, ensure_ascii=False))
    print(f"report: {out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
