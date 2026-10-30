"""M6：越权矩阵、L2 三通道、归因/阶梯、生成清洗、引用、缓存与网关。"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from support.fakes import fake_llm, make_embedding_func, reset_database, seed_default_languages

from src.kms.api.app import create_app
from src.kms.api.session import SESSION_HEADER, SessionSigner, SignedSessionResolver
from src.kms.authority.models import CardDraft
from src.kms.authority.projection import (
    apply_candidate_projection_schema,
    apply_chunk_projection_schema,
)
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.db import Database
from src.kms.publish.pipeline import PublishPipeline
from src.kms.query.cache import InMemoryAnswerCache
from src.kms.query.pipeline import QueryService
from src.kms.scope import SessionScope
from src.kms.storage.backends import build_lightrag
from src.kms.workspace import WorkspaceRegistry

pytestmark = [pytest.mark.m6, pytest.mark.db]

CARDS = [
    (
        "K-US",
        "zh-CN",
        ("SPOT",),
        ("2024",),
        ("Secret",),
        ("BTC",),
        ("USA",),
        "USA BTC 现货规则：单笔限额与风险提示。",
    ),
    (
        "K-UK",
        "zh-CN",
        ("SPOT",),
        ("2024",),
        ("Secret",),
        ("BTC",),
        ("UK",),
        "UK BTC 现货规则：英国市场准入与风险提示。",
    ),
    (
        "K-CON",
        "zh-CN",
        ("CONTRACT",),
        ("2024",),
        ("Secret",),
        ("BTC",),
        ("USA",),
        "CONTRACT 合约规则：杠杆与保证金要求。",
    ),
    (
        "K-2025",
        "zh-CN",
        ("SPOT",),
        ("2025",),
        ("Secret",),
        ("BTC",),
        ("USA",),
        "2025 年度 BTC 现货规则更新。",
    ),
    (
        "K-TS",
        "zh-CN",
        ("SPOT",),
        ("2024",),
        ("TopSecret",),
        ("BTC",),
        ("JP",),
        "日本市场绝密级操作手册。",
    ),
    (
        "K-PUB",
        "zh-CN",
        ("SPOT",),
        ("2024",),
        ("Public",),
        ("BTC",),
        ("KR",),
        "韩国市场公开版风险提示。",
    ),
    (
        "K-MULTI",
        "zh-CN",
        ("SPOT",),
        ("2026",),
        ("Secret",),
        ("BTC",),
        ("USA", "UK"),
        "2026 年美英双市场现货规则。",
    ),
    (
        "K-EN",
        "en-US",
        ("SPOT",),
        ("2024",),
        ("Secret",),
        ("BTC",),
        ("USA",),
        "BTC spot trading rules for the USA market.",
    ),
    (
        "K-ETH",
        "zh-CN",
        ("SPOT",),
        ("2024",),
        ("Secret",),
        ("ETH",),
        ("USA",),
        "ETH 资产规则：以太坊现货风险提示。",
    ),
]
BIZ = {kid: f"BIZ-{kid}" for kid, *_ in CARDS}


def draft(entry) -> CardDraft:
    kid, language, product, year, level, asset, region, body = entry
    return CardDraft(
        kid=kid,
        biz_key=BIZ[kid],
        title=f"卡片 {kid}",
        language=language,
        locale=language,
        product=product,
        year=year,
        security_level=level,
        asset=asset,
        region=region,
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        content={"title": f"卡片 {kid}", "sections": [{"heading": "正文", "body": body}]},
        topic=f"topic-{kid}",
        change_reason=f"reason-{kid}",
    )


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


async def answer_llm(prompt: str) -> str:
    return "根据给定资料，规则如下 [1]。"


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

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
        finally:
            await close_stack(database, rag)

        for language in ("zh-CN", "en-US"):
            database, registry, rag = await make_stack(settings, language)
            try:
                workspace = await registry.resolve(language)
                pipeline = PublishPipeline(
                    settings=settings,
                    database=database,
                    registry=registry,
                    rag=rag,
                    workspace=workspace,
                )
                for entry in CARDS:
                    if entry[1] == language:
                        await pipeline.publish(draft(entry))
            finally:
                await close_stack(database, rag)

    asyncio.run(_prepare())


async def make_service(settings: Settings, language: str = "zh-CN", llm=answer_llm, cache=None):
    query_settings = replace(settings, retrieval=replace(settings.retrieval, answer_min_chunks=1))
    database, registry, rag = await make_stack(query_settings, language)
    service = QueryService(
        settings=query_settings, database=database, registry=registry, rag=rag, llm=llm, cache=cache
    )
    return database, rag, service


def session(language: str = "zh-CN", **overrides: Any) -> SessionScope:
    base = dict(
        language=language,
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    )
    base.update(overrides)
    return SessionScope(**base)


async def test_single_dimension_isolation(settings: Settings) -> None:
    cases = [
        (session(), "BIZ-K-US"),
        (session(region=("UK",)), "BIZ-K-UK"),
        (session(product=("CONTRACT",)), "BIZ-K-CON"),
        (session(year=("2025",)), "BIZ-K-2025"),
        (session(language="en-US"), "BIZ-K-EN"),
    ]
    for scope, expected in cases:
        database, rag, service = await make_service(settings, scope.language)
        try:
            result = await service.answer(scope, "请说明 BTC 现货的交易规则与风险提示")
            assert result.answered, result.as_dict()
            assert result.citations and result.citations[0].biz_key == expected
            assert "[KNOWLEDGE:" not in result.answer
        finally:
            await close_stack(database, rag)


async def test_security_hierarchy_and_adjacent_years(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    try:
        confidential = await service.answer(
            session(security_level="Confidential", region=("KR",)),
            "请说明 BTC 现货的交易规则与风险提示",
        )
        assert confidential.answered and confidential.citations[0].biz_key == "BIZ-K-PUB"
        top_secret = await service.answer(
            session(security_level="TopSecret", region=("JP",)),
            "请说明日本市场绝密级操作手册",
        )
        assert top_secret.answered and top_secret.citations[0].biz_key == "BIZ-K-TS"
        adjacent = await service.answer(session(year=("2025",)), "请说明 2025 年 BTC 现货规则更新")
        assert adjacent.answered and adjacent.citations[0].biz_key == "BIZ-K-2025"
        multi = await service.answer(
            session(year=("2026",), region=("USA", "UK")),
            "请说明 2026 年美英双市场现货规则",
        )
        assert multi.answered and multi.citations[0].biz_key == "BIZ-K-MULTI"
    finally:
        await close_stack(database, rag)


async def test_preference_ladder_and_authorization_cut(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    try:
        ladder = await service.answer(
            session(asset=("USDT",)), "请说明 BTC 现货的交易规则与风险提示"
        )
        assert ladder.answered, ladder.as_dict()
        assert ladder.ladder_steps == 1 and ladder.relaxed_dims == ("asset",)
        assert ladder.citations[0].biz_key == "BIZ-K-US"

        auth_cut = await service.answer(
            session(product=("CONTRACT",), year=("2099",)),
            "请说明合约规则与保证金要求",
        )
        assert auth_cut.refused and auth_cut.shortage == "authorization"
        assert auth_cut.ladder_steps == 0
    finally:
        await close_stack(database, rag)


async def test_asset_isolation_and_unknown_authorization(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    try:
        btc = await service.answer(session(asset=("BTC",)), "请说明 BTC 现货的交易规则")
        eth = await service.answer(session(asset=("ETH",)), "请说明 ETH 资产规则")
        assert btc.answered and btc.citations[0].biz_key == "BIZ-K-US"
        assert eth.answered and eth.citations[0].biz_key == "BIZ-K-ETH"
        unknown = await service.answer(session(product=("FUTURES",)), "请说明规则")
        assert unknown.refused and unknown.shortage == "authorization"
    finally:
        await close_stack(database, rag)


async def test_empty_authorization_session_returns_empty(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    secret = "s" * 40
    signer = SessionSigner(secret)
    resolver = SignedSessionResolver(secret)
    header = signer.sign(
        {
            "language": "zh-CN",
            "product": [],
            "year": [],
            "security_level": [],
            "asset": [],
            "region": [],
            "exp": int(time.time()) + 60,
        }
    )
    try:
        app = create_app(service, resolver)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/answer",
                json={"question": "请说明规则"},
                headers={SESSION_HEADER: header},
            )
            assert response.status_code == 200
            assert response.json()["answered"] is False
    finally:
        await close_stack(database, rag)


async def test_withdrawn_card_is_not_retrievable(settings: Settings) -> None:
    database, registry, rag = await make_stack(settings)
    try:
        workspace = await registry.resolve("zh-CN")
        pipeline = PublishPipeline(
            settings=settings,
            database=database,
            registry=registry,
            rag=rag,
            workspace=workspace,
        )
        await pipeline.withdraw("K-ETH", language="zh-CN")
    finally:
        await close_stack(database, rag)
    database, rag, service = await make_service(settings)
    try:
        result = await service.answer(session(asset=("ETH",)), "请说明 ETH 资产规则")
        assert all(citation.biz_key != "BIZ-K-ETH" for citation in result.citations)
    finally:
        await close_stack(database, rag)


async def test_l2_missing_snapshot_fail_closed(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    saved: list[asyncpg.Record] = []
    try:
        async with database.acquire() as connection:
            saved = await connection.fetch("SELECT * FROM chunk_filter WHERE kid = 'K-2025'")
            await connection.execute("DELETE FROM chunk_filter WHERE kid = 'K-2025'")
        result = await service.answer(session(year=("2025",)), "请说明 2025 年规则")
        assert result.refused
        assert not result.answered
    finally:
        if saved:
            async with database.acquire() as connection:
                for row in saved:
                    await connection.execute(
                        """
                        INSERT INTO chunk_filter (
                            chunk_id, kid, kid_version, product, year, security_level,
                            asset, region, publish_state, valid_from, valid_to, scope_sig
                        ) VALUES ($1,$2,$3,$4::text[],$5::text[],$6::text[],$7::text[],
                                  $8::text[],$9,$10,$11,
                                  ics_scope_sig($4::text[],$5::text[],$6::text[],$9,$7::text[],$8::text[]))
                        ON CONFLICT DO NOTHING
                        """,
                        row["chunk_id"],
                        row["kid"],
                        row["kid_version"],
                        row["product"],
                        row["year"],
                        row["security_level"],
                        row["asset"],
                        row["region"],
                        row["publish_state"],
                        row["valid_from"],
                        row["valid_to"],
                    )
        await close_stack(database, rag)


async def test_graph_channels_not_in_prompt(settings: Settings) -> None:
    captured: list[str] = []

    async def capture(prompt: str) -> str:
        captured.append(prompt)
        return "根据给定资料，规则如下 [1]。"

    database, rag, service = await make_service(settings, llm=capture)
    try:
        result = await service.answer(session(), "请说明 BTC 现货的交易规则与风险提示")
        assert result.answered
        assert captured and "BTC 是一种加密资产" not in captured[0]
        assert result.metrics["l2_discarded"]["entities"] > 0
    finally:
        await close_stack(database, rag)


async def test_answer_cache_is_scope_isolated(settings: Settings) -> None:
    calls = {"count": 0}

    async def counting_llm(prompt: str) -> str:
        calls["count"] += 1
        return "根据给定资料，规则如下 [1]。"

    cache = InMemoryAnswerCache()
    cached_settings = replace(settings, enable_answer_cache=True)
    database, rag, service = await make_service(cached_settings, llm=counting_llm, cache=cache)
    try:
        us = await service.answer(session(), "请说明 BTC 现货的交易规则")
        uk = await service.answer(session(region=("UK",)), "请说明 BTC 现货的交易规则")
        assert us.answered and uk.answered
        assert us.citations[0].biz_key != uk.citations[0].biz_key
        assert calls["count"] == 2
        us_again = await service.answer(session(), "请说明 BTC 现货的交易规则")
        assert us_again.cache_hit is True
        assert calls["count"] == 2
    finally:
        await close_stack(database, rag)


async def test_generation_refuses_without_citation_or_with_prefix(settings: Settings) -> None:
    async def no_citation(_prompt: str) -> str:
        return "没有引用的回答"

    database, rag, service = await make_service(settings, llm=no_citation)
    try:
        result = await service.answer(session(), "请说明 BTC 现货的交易规则")
        assert result.refused and result.reason == "no_citation"
    finally:
        await close_stack(database, rag)

    async def prefix_leak(_prompt: str) -> str:
        return "[KNOWLEDGE:K-US] [KNOWLEDGE:K-US] 规则 [1]"

    database, rag, service = await make_service(settings, llm=prefix_leak)
    try:
        result = await service.answer(session(), "请说明 BTC 现货的交易规则")
        assert result.refused and result.reason == "prefix_residual"
    finally:
        await close_stack(database, rag)


async def test_api_gateway_rejects_illegal_language(settings: Settings) -> None:
    database, rag, service = await make_service(settings)
    secret = "s" * 40
    signer = SessionSigner(secret)
    resolver = SignedSessionResolver(secret)

    def headers(language: str) -> dict[str, str]:
        return {
            SESSION_HEADER: signer.sign(
                {
                    "language": language,
                    "product": ["SPOT"],
                    "year": ["2024"],
                    "security_level": "Secret",
                    "asset": ["BTC"],
                    "region": ["USA"],
                    "exp": int(time.time()) + 60,
                }
            )
        }

    try:
        app = create_app(service, resolver)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post(
                "/v1/answer",
                json={"question": "请说明规则"},
                headers=headers("de-DE"),
            )
            assert bad.status_code == 400
            no_session = await client.post("/v1/answer", json={"question": "请说明规则"})
            assert no_session.status_code == 401
            body_scope = await client.post(
                "/v1/answer",
                json={"question": "请说明规则", "security_level": "TopSecret"},
                headers=headers("zh-CN"),
            )
            assert body_scope.status_code == 422
            good = await client.post(
                "/v1/answer",
                json={"question": "请说明 BTC 现货的交易规则"},
                headers=headers("zh-CN"),
            )
            assert good.status_code == 200
            assert good.json()["answered"] is True
    finally:
        await close_stack(database, rag)


def test_rerank_default_off(settings: Settings) -> None:
    assert settings.retrieval.enable_rerank is False
