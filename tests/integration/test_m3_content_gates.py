"""M3 / G1：两条正文闸 ①②、④、按位对齐、PENDING、改版不继承旧 scope。"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest
from support.fakes import make_embedding_func, reset_database, seed_default_languages

from src.kms.authority.models import CardDraft
from src.kms.authority.projection import apply_chunk_projection_schema
from src.kms.authority.repository import add_card_version, create_card
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.publish.backfill import (
    ChunkScopeRow,
    assert_same_scope,
    backfill_chunk_tables,
    merge_chunk_filter,
    read_doc_chunk_ids,
)
from src.kms.scope import ScopeContext, SessionScope, start_request_metrics
from src.kms.storage.backends import build_lightrag
from src.kms.storage.kv import build_text_chunks_sql, reorder_to
from src.kms.storage.vector import build_vector_query_sql, build_vectors_by_ids_sql

pytestmark = [pytest.mark.m3, pytest.mark.db]

DOC_ID = "M3-DOC"
CARD_KID = "M3-CARD"
VALID_AT = datetime(2024, 6, 1, tzinfo=UTC)
TABLES: dict[str, str] = {}
CHUNK_IDS: list[str] = []
CHUNK0_CONTENT = ""
OTHER_CHUNK_IDS: list[str] = []
OTHER_CONTENT = ""


def make_draft(kid: str = CARD_KID, **overrides: object) -> CardDraft:
    base = dict(
        kid=kid,
        biz_key=f"BIZ-{kid}",
        title=f"卡片 {kid}",
        language="zh-CN",
        locale="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level=("Secret",),
        asset=("BTC",),
        region=("USA",),
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        content={"title": f"卡片 {kid}", "sections": [{"heading": "正文", "body": f"正文 {kid}"}]},
        topic=f"topic-{kid}",
        change_reason=f"reason-{kid}",
    )
    base.update(overrides)
    return CardDraft(**base)  # type: ignore[arg-type]


def ast(product: tuple[str, ...] = ("SPOT",)):
    return SessionScope(
        language="zh-CN",
        product=product,
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    ).to_ast(valid_at=VALID_AT)


async def make_rag(settings: Settings):
    rag = build_lightrag(
        settings,
        workspace="zh-CN",
        embedding_func=make_embedding_func(),
        llm_model_func=lambda prompt, **_kwargs: _llm_result(),
        cosine_threshold=-1.0,
    )
    await rag.initialize_storages()
    return rag


async def _llm_result() -> str:
    return "ok"


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

    async def _prepare() -> None:
        global CHUNK_IDS, CHUNK0_CONTENT, OTHER_CHUNK_IDS, OTHER_CONTENT
        await reset_database(settings)
        await seed_default_languages(settings)
        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            await apply_authority_schema(connection)
        finally:
            await connection.close()

        rag = await make_rag(settings)
        await rag.finalize_storages()

        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            TABLES.update(
                await apply_chunk_projection_schema(
                    connection, model_name="test-embed", embedding_dim=32
                )
            )
        finally:
            await connection.close()

        rag = await make_rag(settings)
        try:
            body = "现货交易规则：BTC 现货在 USA 地区可交易。" * 300
            await rag.apipeline_enqueue_documents(
                [body], ids=[DOC_ID], file_paths=[DOC_ID], process_options="F!"
            )
            await rag.apipeline_process_enqueue_documents()
        finally:
            await rag.finalize_storages()

        rag = await make_rag(settings)
        try:
            other_body = "合约交易规则：ETH 合约在 JP 地区仅限合格投资者。" * 300
            await rag.apipeline_enqueue_documents(
                [other_body], ids=["M3-DOC-B"], file_paths=["M3-DOC-B"], process_options="F!"
            )
            await rag.apipeline_process_enqueue_documents()
        finally:
            await rag.finalize_storages()

        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            OTHER_CHUNK_IDS = await read_doc_chunk_ids(
                connection, workspace="zh-CN", doc_id="M3-DOC-B"
            )
            other_row = await connection.fetchrow(
                "SELECT content FROM LIGHTRAG_DOC_CHUNKS WHERE workspace=$1 AND id=$2",
                "zh-CN",
                OTHER_CHUNK_IDS[0],
            )
            OTHER_CONTENT = other_row["content"]
            other_draft = make_draft(
                kid="M3-CARD-B",
                product=("CONTRACT",),
                asset=("ETH",),
                region=("JP",),
                content={
                    "title": "卡片 M3-CARD-B",
                    "sections": [{"heading": "正文", "body": "合约正文"}],
                },
            )
            await create_card(connection, other_draft)
            other_scope = ast(product=("CONTRACT",))
            other_rows = [
                ChunkScopeRow.from_ast(
                    chunk_id=chunk_id,
                    kid="M3-CARD-B",
                    kid_version=1,
                    ast=other_scope,
                    valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                    valid_to=None,
                )
                for chunk_id in OTHER_CHUNK_IDS
            ]
            await merge_chunk_filter(connection, other_rows)
            await backfill_chunk_tables(
                connection, tables=TABLES, workspace="zh-CN", rows=other_rows
            )
            CHUNK_IDS = await read_doc_chunk_ids(connection, workspace="zh-CN", doc_id=DOC_ID)
            assert len(CHUNK_IDS) >= 2
            row = await connection.fetchrow(
                "SELECT content FROM LIGHTRAG_DOC_CHUNKS WHERE workspace=$1 AND id=$2",
                "zh-CN",
                CHUNK_IDS[0],
            )
            CHUNK0_CONTENT = row["content"]
            await create_card(connection, make_draft())
            scope = ast()
            rows = [
                ChunkScopeRow.from_ast(
                    chunk_id=CHUNK_IDS[0],
                    kid=CARD_KID,
                    kid_version=1,
                    ast=scope,
                    valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                    valid_to=None,
                )
            ]
            await merge_chunk_filter(connection, rows)
            updated = await backfill_chunk_tables(
                connection, tables=TABLES, workspace="zh-CN", rows=rows
            )
            assert updated == {"doc_chunks": 1, "vdb_chunks": 1}
        finally:
            await connection.close()

    asyncio.run(_prepare())


async def test_deny_default_invisible_to_both_content_gates(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        with ScopeContext(ast()):
            ordered = await rag.text_chunks.get_by_ids([CHUNK_IDS[0], CHUNK_IDS[1]])
            assert ordered[0] is not None
            assert ordered[1] is None
            assert await rag.text_chunks.get_by_id(CHUNK_IDS[1]) is None
            results = await rag.chunks_vdb.query(CHUNK0_CONTENT, top_k=10)
            ids = {row["id"] for row in results}
            assert CHUNK_IDS[0] in ids and CHUNK_IDS[1] not in ids
            vectors = await rag.chunks_vdb.get_vectors_by_ids([CHUNK_IDS[0], CHUNK_IDS[1]])
            assert set(vectors) == {CHUNK_IDS[0]}
        without_scope = await rag.text_chunks.get_by_ids([CHUNK_IDS[0], CHUNK_IDS[1]])
        assert without_scope[0] is not None and without_scope[1] is not None
    finally:
        await rag.finalize_storages()


async def test_other_scope_card_invisible_on_both_gates(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        with ScopeContext(ast()):
            rows = await rag.text_chunks.get_by_ids([OTHER_CHUNK_IDS[0]])
            assert rows[0] is None
            results = await rag.chunks_vdb.query(OTHER_CONTENT, top_k=10)
            assert not ({row["id"] for row in results} & set(OTHER_CHUNK_IDS))
            vectors = await rag.chunks_vdb.get_vectors_by_ids([OTHER_CHUNK_IDS[0]])
            assert vectors == {}
        without_scope = await rag.text_chunks.get_by_ids([OTHER_CHUNK_IDS[0]])
        assert without_scope[0] is not None
    finally:
        await rag.finalize_storages()


async def test_projection_ddl_is_idempotent(settings: Settings) -> None:
    connection = await asyncpg.connect(settings.postgres.dsn())
    try:
        from src.kms.authority.projection import (
            apply_candidate_projection_schema,
            apply_chunk_projection_schema,
        )

        await apply_chunk_projection_schema(connection, model_name="test-embed", embedding_dim=32)
        await apply_chunk_projection_schema(connection, model_name="test-embed", embedding_dim=32)
        await apply_candidate_projection_schema(
            connection, model_name="test-embed", embedding_dim=32
        )
        await apply_candidate_projection_schema(
            connection, model_name="test-embed", embedding_dim=32
        )
    finally:
        await connection.close()


async def test_pending_vector_ids_fail_closed(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        await rag.chunks_vdb.upsert(
            {
                "pending-1": {
                    "content": "pending 正文",
                    "file_path": "pending",
                }
            }
        )
        metrics, token = start_request_metrics()
        try:
            with ScopeContext(ast()):
                vectors = await rag.chunks_vdb.get_vectors_by_ids(["pending-1"])
        finally:
            from src.kms.scope import reset_request_metrics

            reset_request_metrics(token)
        assert vectors == {}
        assert metrics.l1_pending_dropped == 1
    finally:
        await rag.chunks_vdb.drop_pending_index_ops()
        await rag.finalize_storages()


async def test_reorder_to_preserves_positions(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        with ScopeContext(ast()):
            rows = await rag.text_chunks.get_by_ids(["not-exist-1", CHUNK_IDS[0], "not-exist-2"])
        assert rows[0] is None and rows[1] is not None and rows[2] is None
        assert reorder_to(["a", "b"], [{"id": "b"}, {"id": "a"}]) == [{"id": "a"}, {"id": "b"}]
    finally:
        await rag.finalize_storages()


async def test_sql_shape_and_explain(settings: Settings) -> None:
    scope = ast()
    kv_sql, kv_params = build_text_chunks_sql(scope)
    assert "publish_state = $8" in kv_sql and "valid_from" in kv_sql
    assert len(kv_params) == 7  # 五集合维 + publish + validity
    vector_sql, _vector_params = build_vector_query_sql(
        "chunks", "LIGHTRAG_VDB_CHUNKS_test_embed_32d", scope
    )
    assert vector_sql.index(" AND ") < vector_sql.index("ORDER BY")
    assert "publish_state = $10" in vector_sql
    ids_sql, ids_params = build_vectors_by_ids_sql(
        "LIGHTRAG_VDB_CHUNKS_test_embed_32d", scope, for_chunks=True
    )
    assert " AND " in ids_sql and len(ids_params) == 7
    connection = await asyncpg.connect(settings.postgres.dsn())
    try:
        plan = await connection.fetch(
            "EXPLAIN (FORMAT JSON) " + kv_sql,
            "zh-CN",
            [CHUNK_IDS[0]],
            *kv_params,
        )
        plan_text = str(plan[0][0])
        assert "Seq Scan" in plan_text or "Index" in plan_text
        assert "publish_state" in plan_text
        assert (
            await assert_same_scope(
                connection, tables=TABLES, workspace="zh-CN", chunk_ids=CHUNK_IDS[:1]
            )
            == []
        )
    finally:
        await connection.close()


async def test_revision_does_not_inherit_old_scope(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        result = await rag.adelete_by_doc_id(DOC_ID)
        assert result.status == "success"
        body = "现货交易规则：BTC 现货在 USA 地区可交易。" * 300
        await rag.apipeline_enqueue_documents(
            [body], ids=[DOC_ID], file_paths=[DOC_ID], process_options="F!"
        )
        await rag.apipeline_process_enqueue_documents()
    finally:
        await rag.finalize_storages()

    connection = await asyncpg.connect(settings.postgres.dsn())
    try:
        new_ids = await read_doc_chunk_ids(connection, workspace="zh-CN", doc_id=DOC_ID)
        assert new_ids == CHUNK_IDS
        await add_card_version(
            connection,
            make_draft(product=("CONTRACT",), change_reason="revision to contract"),
            version=2,
            published_at=None,
        )
        new_scope = ast(product=("CONTRACT",))
        rows = [
            ChunkScopeRow.from_ast(
                chunk_id=chunk_id,
                kid=CARD_KID,
                kid_version=2,
                ast=new_scope,
                valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                valid_to=None,
            )
            for chunk_id in new_ids
        ]
        await merge_chunk_filter(connection, rows)
        await backfill_chunk_tables(connection, tables=TABLES, workspace="zh-CN", rows=rows)
        history = await connection.fetch(
            "SELECT kid_version FROM chunk_filter WHERE chunk_id = $1 ORDER BY kid_version",
            new_ids[0],
        )
        assert [row["kid_version"] for row in history] == [1, 2]
    finally:
        await connection.close()

    rag = await make_rag(settings)
    try:
        with ScopeContext(ast(product=("SPOT",))):
            old_rows = await rag.text_chunks.get_by_ids([CHUNK_IDS[0]])
            old_results = await rag.chunks_vdb.query(CHUNK0_CONTENT, top_k=10)
        assert old_rows[0] is None and old_results == []
        with ScopeContext(ast(product=("CONTRACT",))):
            new_rows = await rag.text_chunks.get_by_ids([CHUNK_IDS[0]])
            new_results = await rag.chunks_vdb.query(CHUNK0_CONTENT, top_k=10)
        assert new_rows[0] is not None and set(CHUNK_IDS) <= {row["id"] for row in new_results}
    finally:
        await rag.finalize_storages()
