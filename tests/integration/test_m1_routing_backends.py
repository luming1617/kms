"""M1 退出证据：路由拒收、四后端重指向、启动自检、重启持久化。"""

from __future__ import annotations

import os

import pytest
from lightrag.kg.postgres_impl import ClientManager
from support.fakes import make_embedding_func, reset_database, seed_default_languages

from src.kms.api.gateway import GatewayRejection, resolve_request_workspace
from src.kms.config import Settings
from src.kms.db import Database
from src.kms.scope import ScopeContaminationError, ScopeContext, SessionScope
from src.kms.storage.backends import (
    BackendWiringError,
    assert_backend_wiring,
    build_lightrag,
)
from src.kms.workspace import WorkspaceRegistry, WorkspaceRejected

pytestmark = [pytest.mark.m1, pytest.mark.db]


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

    asyncio.run(reset_database(settings))
    asyncio.run(seed_default_languages(settings))


async def test_registry_exact_match_and_disabled(settings: Settings) -> None:
    database = Database(settings)
    await database.connect()
    try:
        registry = WorkspaceRegistry(database)
        record = await registry.resolve("zh-CN")
        assert record.enabled is True
        with pytest.raises(WorkspaceRejected):
            await registry.resolve("zh-cn")
        with pytest.raises(WorkspaceRejected):
            await registry.resolve("de-DE")
        await database.execute("UPDATE workspace_registry SET enabled = false WHERE lang = 'en-US'")
        with pytest.raises(WorkspaceRejected):
            await registry.resolve("en-US")
        await database.execute("UPDATE workspace_registry SET enabled = true WHERE lang = 'en-US'")
    finally:
        await database.close()


async def test_gateway_rejects_illegal_language(settings: Settings) -> None:
    database = Database(settings)
    await database.connect()
    try:
        registry = WorkspaceRegistry(database)
        with pytest.raises(GatewayRejection) as exc:
            await resolve_request_workspace(registry, "../etc")
        assert exc.value.status_code == 400
    finally:
        await database.close()


async def test_four_backends_are_pg_and_self_checked(settings: Settings) -> None:
    embedding = make_embedding_func(dim=32, model_name="test-embed")

    async def llm(prompt: str, **_kwargs: object) -> str:
        return "ok"

    rag = build_lightrag(settings, workspace="zh-CN", embedding_func=embedding, llm_model_func=llm)
    wiring = assert_backend_wiring(rag)
    assert wiring["storages"]["kv_storage"].startswith("kms.storage.kv.")
    assert wiring["storages"]["vector_storage"].startswith("kms.storage.vector.")
    assert wiring["storages"]["graph_storage"].startswith("kms.storage.graph.")
    assert wiring["doc_status"].startswith("lightrag.kg.postgres_impl.")
    assert ClientManager.get_config(vector_storage="PGVectorStorage")["enable_vector"] is True


async def test_postgres_workspace_non_empty_fails_startup(settings: Settings) -> None:
    old = os.environ.get("POSTGRES_WORKSPACE")
    os.environ["POSTGRES_WORKSPACE"] = "zh-CN"
    try:
        with pytest.raises(Exception):
            settings.validate_startup()
        with pytest.raises(BackendWiringError):
            assert_backend_wiring(
                type(
                    "Rag",
                    (),
                    {
                        "kv_storage": None,
                        "vector_storage": None,
                        "chunk_entity_relation_graph": None,
                        "doc_status": None,
                    },
                )()
            )
    finally:
        if old is None:
            os.environ.pop("POSTGRES_WORKSPACE", None)
        else:
            os.environ["POSTGRES_WORKSPACE"] = old


async def test_min_rerank_and_per_language_embedding(settings: Settings) -> None:
    async def llm(_prompt: str, **_kwargs: object) -> str:
        return "ok"

    rag = build_lightrag(
        settings,
        workspace="zh-CN",
        embedding_func=make_embedding_func(dim=32, model_name="test-embed"),
        llm_model_func=llm,
    )
    assert rag.min_rerank_score == settings.retrieval.min_rerank_score
    other = build_lightrag(
        settings,
        workspace="zh-CN",
        embedding_func=make_embedding_func(dim=64, model_name="other-embed"),
        llm_model_func=llm,
    )
    assert other.workspace == "zh-CN"
    wiring = assert_backend_wiring(other)
    assert wiring["embedding"]["model"] == "other-embed"


async def test_restart_persistence_and_write_guard(settings: Settings) -> None:
    embedding = make_embedding_func(dim=32, model_name="test-embed")

    async def llm(prompt: str, **_kwargs: object) -> str:
        return "ok"

    rag = build_lightrag(settings, workspace="zh-CN", embedding_func=embedding, llm_model_func=llm)
    await rag.initialize_storages()
    try:
        await rag.chunk_entity_relation_graph.upsert_node(
            "BTC", {"entity_id": "BTC", "description": "加密资产"}
        )
        await rag.apipeline_enqueue_documents(
            ["持久化烟囱测试正文。"], ids=["m1-doc"], file_paths=["m1-doc"], process_options="F!"
        )
        await rag.apipeline_process_enqueue_documents()
        node = await rag.chunk_entity_relation_graph.get_node("BTC")
        assert node is not None
    finally:
        await rag.finalize_storages()

    rag2 = build_lightrag(settings, workspace="zh-CN", embedding_func=embedding, llm_model_func=llm)
    await rag2.initialize_storages()
    try:
        node = await rag2.chunk_entity_relation_graph.get_node("BTC")
        rows = await rag2.text_chunks.get_by_ids(["m1-doc-chunk-000"])
        status = await rag2.doc_status.get_by_id("m1-doc")
        assert node is not None
        assert rows and rows[0] is not None
        assert status is not None and status["status"] == "processed"
    finally:
        await rag2.finalize_storages()


async def test_scope_context_blocks_write_paths(settings: Settings) -> None:
    embedding = make_embedding_func(dim=32, model_name="test-embed")

    async def llm(prompt: str, **_kwargs: object) -> str:
        return "ok"

    rag = build_lightrag(settings, workspace="zh-CN", embedding_func=embedding, llm_model_func=llm)
    await rag.initialize_storages()
    try:
        scope = SessionScope(
            language="zh-CN", product=["SPOT"], year=["2024"], security_level="Secret"
        ).to_ast()
        with ScopeContext(scope), pytest.raises(ScopeContaminationError):
            await rag.chunk_entity_relation_graph.upsert_node("X", {"entity_id": "X"})
    finally:
        await rag.finalize_storages()
