"""M7：nightly 连续对账无差异、小样本全链路压测、指标有输出。"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from support.fakes import fake_llm, make_embedding_func, reset_database, seed_default_languages

from src.kms.authority.models import CardDraft
from src.kms.authority.projection import (
    apply_candidate_projection_schema,
    apply_chunk_projection_schema,
)
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.db import Database
from src.kms.observability.metrics import render_metrics
from src.kms.publish.pipeline import PublishPipeline
from src.kms.publish.reconcile import reconcile_language
from src.kms.query.pipeline import QueryService
from src.kms.scope import SessionScope
from src.kms.storage.backends import build_lightrag
from src.kms.workspace import WorkspaceRegistry

pytestmark = [pytest.mark.m7, pytest.mark.db]

REGIONS = ["USA", "UK", "JP", "KR", "HK"]
CARDS = [f"M7-K{i}" for i in range(len(REGIONS))]


async def make_stack(settings: Settings, language: str = "zh-CN"):
    database = Database(settings)
    await database.connect()
    registry = WorkspaceRegistry(database)
    rag = build_lightrag(
        settings,
        workspace=language,
        embedding_func=make_embedding_func(),
        llm_model_func=fake_llm,
        cosine_threshold=-1.0,
    )
    await rag.initialize_storages()
    return database, registry, rag


async def close_stack(database: Database, rag) -> None:
    await rag.finalize_storages()
    await database.close()


def draft(kid: str, region: str, body: str) -> CardDraft:
    return CardDraft(
        kid=kid,
        biz_key=f"BIZ-{kid}",
        title=f"卡片 {kid}",
        language="zh-CN",
        locale="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level=("Secret",),
        asset=("BTC",),
        region=(region,),
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        content={"title": f"卡片 {kid}", "sections": [{"heading": "正文", "body": body}]},
        topic=f"topic-{kid}",
        change_reason=f"reason-{kid}",
    )


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    async def _prepare() -> None:
        await reset_database(settings)
        await seed_default_languages(settings)
        database, registry, rag = await make_stack(settings)
        try:
            async with database.transaction() as connection:
                await apply_authority_schema(connection)
                await apply_chunk_projection_schema(
                    connection, model_name="test-embed", embedding_dim=32
                )
                await apply_candidate_projection_schema(
                    connection, model_name="test-embed", embedding_dim=32
                )
            workspace = await registry.resolve("zh-CN")
            pipeline = PublishPipeline(
                settings=settings,
                database=database,
                registry=registry,
                rag=rag,
                workspace=workspace,
            )
            for kid, region in zip(CARDS, REGIONS, strict=True):
                await pipeline.publish(draft(kid, region, f"{region} 市场 BTC 现货规则与风险提示"))
        finally:
            await close_stack(database, rag)

    asyncio.run(_prepare())


async def test_nightly_reconcile_is_stable(settings: Settings) -> None:
    database, _, rag = await make_stack(settings)
    try:
        async with database.acquire() as connection:
            first = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
            second = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
        assert first.ok and second.ok
        for name in first.criteria:
            assert first.criteria[name].count == second.criteria[name].count == 0
    finally:
        await close_stack(database, rag)


async def test_concurrent_load_reports_qps(settings: Settings) -> None:
    query_settings = replace(settings, retrieval=replace(settings.retrieval, answer_min_chunks=1))
    database, registry, rag = await make_stack(query_settings)

    async def llm(_prompt: str) -> str:
        return "根据资料，规则如下 [1]。"

    service = QueryService(
        settings=query_settings, database=database, registry=registry, rag=rag, llm=llm
    )
    try:

        async def one(index: int) -> tuple[bool, float]:
            scope = SessionScope(
                language="zh-CN",
                product=("SPOT",),
                year=("2024",),
                security_level="Secret",
                asset=("BTC",),
                region=(REGIONS[index % len(REGIONS)],),
            )
            started = time.perf_counter()
            result = await service.answer(scope, "请说明 BTC 现货的交易规则与风险提示")
            return result.answered, (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        results = await asyncio.gather(*(one(i) for i in range(30)))
        elapsed = time.perf_counter() - started
        latencies = sorted(item[1] for item in results)
        p95 = latencies[round(0.95 * (len(latencies) - 1))]
        assert all(item[0] for item in results)
        assert p95 < 5000
        assert 30 / elapsed > 0
        print(f"concurrent qps={30 / elapsed:.1f} p95={p95:.0f}ms")
    finally:
        await close_stack(database, rag)


async def test_small_load_qps_and_metrics(settings: Settings) -> None:
    query_settings = replace(settings, retrieval=replace(settings.retrieval, answer_min_chunks=1))
    database, registry, rag = await make_stack(query_settings)

    async def llm(_prompt: str) -> str:
        return "根据资料，规则如下 [1]。"

    service = QueryService(
        settings=query_settings, database=database, registry=registry, rag=rag, llm=llm
    )
    try:
        latencies: list[float] = []
        errors = 0
        started = time.perf_counter()
        for index in range(30):
            scope = SessionScope(
                language="zh-CN",
                product=("SPOT",),
                year=("2024",),
                security_level="Secret",
                asset=("BTC",),
                region=(REGIONS[index % len(REGIONS)],),
            )
            query_started = time.perf_counter()
            result = await service.answer(scope, "请说明 BTC 现货的交易规则与风险提示")
            latencies.append((time.perf_counter() - query_started) * 1000)
            if not result.answered:
                errors += 1
        elapsed = time.perf_counter() - started
        sorted_latencies = sorted(latencies)
        p95 = sorted_latencies[round(0.95 * (len(sorted_latencies) - 1))]
        metrics_text = render_metrics()[0].decode("utf-8")
        assert errors == 0
        assert p95 < 5000
        assert "knowledge_l2_channel_entered_total" in metrics_text
        assert "knowledge_api_http_requests_total" in metrics_text
        assert statistics.mean(latencies) > 0
        assert 30 / elapsed > 0
    finally:
        await close_stack(database, rag)
