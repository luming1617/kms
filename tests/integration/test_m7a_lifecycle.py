"""M7a 生命周期：改版不引用 V1、撤回不可检索、迟到写被拒。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from support.fakes import fake_llm, make_embedding_func, reset_database, seed_default_languages

from eval.lifecycle import run_lifecycle_case
from src.kms.authority.models import CardDraft
from src.kms.authority.projection import (
    apply_candidate_projection_schema,
    apply_chunk_projection_schema,
)
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.db import Database
from src.kms.publish.pipeline import PublishPipeline
from src.kms.query.pipeline import QueryService
from src.kms.scope import SessionScope
from src.kms.storage.backends import build_lightrag
from src.kms.workspace import WorkspaceRegistry

pytestmark = [pytest.mark.m7a, pytest.mark.db]


def draft(marker: str) -> CardDraft:
    return CardDraft(
        kid="M7A-LIFE",
        biz_key="BIZ-M7A-LIFE",
        title="生命周期卡",
        language="zh-CN",
        locale="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level=("Secret",),
        asset=("BTC",),
        region=("USA",),
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        content={
            "title": "生命周期卡",
            "sections": [{"heading": "正文", "body": f"{marker} 规则"}],
        },
        topic="life",
        change_reason=marker,
    )


async def make_stack(settings: Settings):
    database = Database(settings)
    await database.connect()
    registry = WorkspaceRegistry(database)
    rag = build_lightrag(
        settings,
        workspace="zh-CN",
        embedding_func=make_embedding_func(),
        llm_model_func=fake_llm,
        cosine_threshold=-1.0,
    )
    await rag.initialize_storages()
    return database, registry, rag


async def test_revision_withdraw_late_write(settings: Settings) -> None:
    query_settings = replace(settings, retrieval=replace(settings.retrieval, answer_min_chunks=1))
    await reset_database(settings)
    await seed_default_languages(settings)
    database, registry, rag = await make_stack(query_settings)
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
            settings=query_settings,
            database=database,
            registry=registry,
            rag=rag,
            workspace=workspace,
        )

        async def answer_llm(prompt: str) -> str:
            marker = "CANARY-V1" if "CANARY-V1" in prompt else "CANARY-V2"
            return f"{marker} [1]"

        service = QueryService(
            settings=query_settings,
            database=database,
            registry=registry,
            rag=rag,
            llm=answer_llm,
        )
        session = SessionScope(
            language="zh-CN",
            product=("SPOT",),
            year=("2024",),
            security_level="Secret",
            asset=("BTC",),
            region=("USA",),
        )
        outcome = await run_lifecycle_case(
            pipeline=pipeline,
            service=service,
            draft_v1=draft("CANARY-V1"),
            draft_v2=draft("CANARY-V2"),
            session=session,
            question="请说明 CANARY 规则",
        )
        assert outcome.current_marker == "CANARY-V2"
        assert outcome.old_marker_retrievable is False
        assert outcome.withdrawn_retrievable is False
        assert outcome.late_write_overrode is False
    finally:
        await rag.finalize_storages()
        await database.close()
