"""M5 / G2：发布→对账→撤回→重发；幂等、冲突队列、冻结、fail-closed。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from support.fakes import fake_llm, make_embedding_func, reset_database, seed_default_languages

from src.kms.authority.models import CardDraft
from src.kms.authority.projection import (
    apply_candidate_projection_schema,
    apply_chunk_projection_schema,
    resolve_projection_tables,
)
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.db import Database
from src.kms.publish.pipeline import (
    PublishConflictError,
    PublishFailed,
    PublishFrozenError,
    PublishPipeline,
)
from src.kms.publish.reconcile import reconcile_language
from src.kms.storage.backends import build_lightrag
from src.kms.workspace import WorkspaceRegistry

pytestmark = [pytest.mark.m5, pytest.mark.db]

BASE_VALID_FROM = datetime(2024, 1, 1, tzinfo=UTC)


def draft(
    kid: str, *, year: str = "2024", body: str | None = None, **overrides: object
) -> CardDraft:
    base = dict(
        kid=kid,
        biz_key=f"BIZ-{kid}",
        title=f"卡片 {kid}",
        language="zh-CN",
        locale="zh-CN",
        product=("SPOT",),
        year=(year,),
        security_level=("Secret",),
        asset=("BTC",),
        region=("USA",),
        valid_from=BASE_VALID_FROM,
        content={
            "title": f"卡片 {kid}",
            "sections": [{"heading": "正文", "body": body or f"正文 {kid}"}],
        },
        topic=f"topic-{kid}",
        change_reason=f"reason-{kid}",
    )
    base.update(overrides)
    return CardDraft(**base)  # type: ignore[arg-type]


async def make_pipeline(settings: Settings):
    database = Database(settings)
    await database.connect()
    registry = WorkspaceRegistry(database)
    embedding = make_embedding_func()
    rag = build_lightrag(
        settings, workspace="zh-CN", embedding_func=embedding, llm_model_func=fake_llm
    )
    await rag.initialize_storages()
    workspace = await registry.resolve("zh-CN")
    pipeline = PublishPipeline(
        settings=settings, database=database, registry=registry, rag=rag, workspace=workspace
    )
    return database, rag, pipeline


async def close_pipeline(database: Database, rag) -> None:
    await rag.finalize_storages()
    await database.close()


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

    async def _prepare() -> None:
        await reset_database(settings)
        await seed_default_languages(settings)
        database = Database(settings)
        await database.connect()
        try:
            async with database.transaction() as connection:
                await apply_authority_schema(connection)
        finally:
            await database.close()
        database, rag, _ = await make_pipeline(settings)
        try:
            async with database.acquire() as connection:
                await apply_chunk_projection_schema(
                    connection, model_name="test-embed", embedding_dim=32
                )
                await apply_candidate_projection_schema(
                    connection, model_name="test-embed", embedding_dim=32
                )
        finally:
            await close_pipeline(database, rag)

    asyncio.run(_prepare())


async def test_publish_new_card_reconcile_and_visibility(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        result = await pipeline.publish(draft("M5-K1", year="2024"))
        assert result.status == "published" and result.version == 1
        assert result.chunk_count > 0 and result.reconcile_ok
        async with database.acquire() as connection:
            report = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
            assert report.ok, {k: (v.count, v.samples) for k, v in report.criteria.items()}
            rows = await connection.fetch(
                "SELECT publish_state, kid_version FROM lightrag_doc_chunks "
                "WHERE workspace=$1 AND full_doc_id=$2",
                "zh-CN",
                "M5-K1",
            )
            assert rows and all(row["publish_state"] == "PUBLISHED" for row in rows)
    finally:
        await close_pipeline(database, rag)


async def test_replay_same_publish_is_idempotent(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        first = await pipeline.publish(draft("M5-K2", year="2025"))
        second = await pipeline.publish(draft("M5-K2", year="2025"))
        assert first.status == "published" and second.status == "unchanged"
        async with database.acquire() as connection:
            versions = await connection.fetchval(
                "SELECT count(*) FROM knowledge_version WHERE kid=$1", "M5-K2"
            )
            cf_rows = await connection.fetchval(
                "SELECT count(*) FROM chunk_filter WHERE kid=$1", "M5-K2"
            )
        assert versions == 1 and cf_rows == first.chunk_count
    finally:
        await close_pipeline(database, rag)


async def test_revision_withdraw_republish(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        first = await pipeline.publish(draft("M5-K3", year="2026", body="v1 正文"))
        revised = await pipeline.publish(
            draft("M5-K3", year="2026", body="v2 正文", change_reason="v2")
        )
        assert first.version == 1 and revised.version == 2
        async with database.acquire() as connection:
            history = await connection.fetch(
                "SELECT DISTINCT kid_version FROM chunk_filter WHERE kid=$1 ORDER BY kid_version",
                "M5-K3",
            )
            assert [row["kid_version"] for row in history] == [1, 2]

        withdrawn = await pipeline.withdraw("M5-K3", language="zh-CN")
        assert withdrawn.status == "withdrawn"
        async with database.acquire() as connection:
            state = await connection.fetchval(
                "SELECT publish_state FROM knowledge WHERE kid=$1", "M5-K3"
            )
            projection = await connection.fetchval(
                "SELECT count(*) FROM lightrag_doc_chunks WHERE workspace=$1 AND full_doc_id=$2",
                "zh-CN",
                "M5-K3",
            )
            report = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
        assert state == "DEPRECATED" and projection == 0
        assert report.ok, {k: (v.count, v.samples) for k, v in report.criteria.items()}

        republished = await pipeline.publish(
            draft("M5-K3", year="2026", body="v2 正文", change_reason="republish")
        )
        assert republished.status == "published" and republished.version == 3
    finally:
        await close_pipeline(database, rag)


async def test_conflict_enters_manual_queue(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        await pipeline.publish(
            draft(
                "M5-K4",
                year="2024",
                body="A 答案",
                product=("CONTRACT",),
                asset=("ETH",),
                region=("JP",),
            )
        )
        with pytest.raises(PublishConflictError):
            await pipeline.publish(
                draft(
                    "M5-K5",
                    year="2024",
                    body="B 答案",
                    product=("CONTRACT",),
                    asset=("ETH",),
                    region=("JP",),
                )
            )
        async with database.acquire() as connection:
            queued = await connection.fetchrow(
                "SELECT kid, status FROM conflict_review_queue WHERE kid='M5-K5'"
            )
        assert queued is not None and queued["status"] == "PENDING"
    finally:
        await close_pipeline(database, rag)


async def test_reconcile_drift_freezes_language(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        await pipeline.publish(
            draft(
                "M5-K6",
                year="2026",
                body="冻结测试",
                product=("CONTRACT",),
                asset=("ETH",),
                region=("JP",),
            )
        )
        async with database.acquire() as connection:
            await connection.execute(
                "UPDATE lightrag_doc_chunks SET publish_state='PENDING' "
                "WHERE workspace=$1 AND full_doc_id=$2",
                "zh-CN",
                "M5-K6",
            )
            report = await reconcile_language(connection, "zh-CN", freeze_on_failure=True)
        assert not report.ok and report.frozen
        with pytest.raises(PublishFrozenError):
            await pipeline.publish(
                draft(
                    "M5-K7",
                    year="2025",
                    body="冻结后新卡",
                    product=("CONTRACT",),
                    asset=("ETH",),
                    region=("JP",),
                )
            )
        async with database.acquire() as connection:
            await connection.execute(
                "UPDATE lightrag_doc_chunks SET publish_state='PUBLISHED' "
                "WHERE workspace=$1 AND full_doc_id=$2",
                "zh-CN",
                "M5-K6",
            )
            await connection.execute(
                "UPDATE workspace_publish_freeze SET frozen=false WHERE lang='zh-CN'"
            )
    finally:
        await close_pipeline(database, rag)


async def test_publish_failure_leaves_fail_closed(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ingest boom")

    original = rag.ainsert
    rag.ainsert = boom  # type: ignore[method-assign]
    try:
        with pytest.raises(PublishFailed):
            await pipeline.publish(
                draft(
                    "M5-K8",
                    year="2025",
                    body="失败正文",
                    product=("CONTRACT",),
                    asset=("ETH",),
                    region=("JP",),
                )
            )
    finally:
        rag.ainsert = original  # type: ignore[method-assign]
    try:
        async with database.acquire() as connection:
            card = await connection.fetchval("SELECT kid FROM knowledge WHERE kid='M5-K8'")
            projection = await connection.fetchval(
                "SELECT count(*) FROM lightrag_doc_chunks WHERE workspace=$1 AND full_doc_id=$2",
                "zh-CN",
                "M5-K8",
            )
        assert card is None and projection == 0
    finally:
        await close_pipeline(database, rag)


async def test_unchanged_repairs_projection_drift(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        first = await pipeline.publish(
            draft("M5-DRIFT", year="2026", body="漂移测试", asset=("ETH",), region=("HK",))
        )
        tables = resolve_projection_tables("test-embed", 32)
        async with database.acquire() as connection:
            chunk_id = await connection.fetchval(
                "SELECT id FROM lightrag_doc_chunks WHERE workspace=$1 AND full_doc_id=$2 LIMIT 1",
                "zh-CN",
                "M5-DRIFT",
            )
            await connection.execute(
                f"UPDATE {tables['vdb_chunks']} SET product='{{CONTRACT}}'::text[] "
                "WHERE workspace=$1 AND id=$2",
                "zh-CN",
                chunk_id,
            )
        second = await pipeline.publish(
            draft("M5-DRIFT", year="2026", body="漂移测试", asset=("ETH",), region=("HK",))
        )
        assert second.status == "published"
        assert second.version == first.version + 1
        async with database.acquire() as connection:
            report = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
        assert report.ok, {k: (v.count, v.samples) for k, v in report.criteria.items()}
    finally:
        await close_pipeline(database, rag)


async def test_vector_only_clause_card(settings: Settings) -> None:
    database, rag, pipeline = await make_pipeline(settings)
    try:
        result = await pipeline.publish(
            draft("M5-K9", year="2025", body="条款类正文", asset=("USDT",), region=("KR",)),
            vector_only=True,
        )
        assert result.status == "published" and result.chunk_count > 0
        async with database.acquire() as connection:
            report = await reconcile_language(connection, "zh-CN", freeze_on_failure=False)
            assert report.ok, {k: (v.count, v.samples) for k, v in report.criteria.items()}
    finally:
        await close_pipeline(database, rag)
