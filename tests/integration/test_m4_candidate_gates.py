"""M4：③a/③b/⑤⑥⑦⑧⑨ 候选闸；degree 与批查同谓词；local/global 分支差异。"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest
from lightrag import QueryParam
from support.fakes import make_embedding_func, reset_database, seed_default_languages

from src.kms.authority.projection import apply_candidate_projection_schema
from src.kms.config import Settings
from src.kms.scope import ScopeContext, SessionScope
from src.kms.storage.backends import build_lightrag
from src.kms.storage.graph import (
    build_degrees_sql,
    build_edges_sql,
    build_nodes_edges_sql,
    build_nodes_sql,
)

pytestmark = [pytest.mark.m4, pytest.mark.db]

VALID_AT = datetime(2024, 6, 1, tzinfo=UTC)
NODES_A = ["A1", "A2"]
NODES_B = ["B1", "B2"]


def scope_a():
    return SessionScope(
        language="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    ).to_ast(valid_at=VALID_AT)


def scope_b():
    return SessionScope(
        language="zh-CN",
        product=("CONTRACT",),
        year=("2025",),
        security_level="Public",
        asset=("ETH",),
        region=("JP",),
    ).to_ast(valid_at=VALID_AT)


async def make_rag(settings: Settings):
    async def llm(prompt: str, **_kwargs: object) -> str:
        return "ok"

    rag = build_lightrag(
        settings,
        workspace="zh-CN",
        embedding_func=make_embedding_func(),
        llm_model_func=llm,
        cosine_threshold=-1.0,
    )
    await rag.initialize_storages()
    return rag


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

    async def _prepare() -> None:
        await reset_database(settings)
        await seed_default_languages(settings)
        rag = await make_rag(settings)
        await rag.finalize_storages()
        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            await apply_candidate_projection_schema(
                connection, model_name="test-embed", embedding_dim=32
            )
        finally:
            await connection.close()

        rag = await make_rag(settings)
        try:
            graph = rag.chunk_entity_relation_graph
            for node in NODES_A + NODES_B:
                await graph.upsert_node(node, {"entity_id": node, "description": f"{node} 描述"})
            await graph.upsert_edge(
                "A1", "A2", {"description": "A 边", "keywords": "A", "weight": 1.0}
            )
            await graph.upsert_edge(
                "B1", "B2", {"description": "B 边", "keywords": "B", "weight": 1.0}
            )
            await rag.entities_vdb.upsert(
                {
                    "A1": {"entity_name": "A1", "content": "A1 entity", "source_id": "chunk-a"},
                    "A2": {"entity_name": "A2", "content": "A2 entity", "source_id": "chunk-a"},
                    "B1": {"entity_name": "B1", "content": "B1 entity", "source_id": "chunk-b"},
                    "B2": {"entity_name": "B2", "content": "B2 entity", "source_id": "chunk-b"},
                }
            )
            await rag.relationships_vdb.upsert(
                {
                    "A1-A2": {
                        "src_id": "A1",
                        "tgt_id": "A2",
                        "content": "A1 A2 relation",
                        "source_id": "chunk-a",
                    },
                    "B1-B2": {
                        "src_id": "B1",
                        "tgt_id": "B2",
                        "content": "B1 B2 relation",
                        "source_id": "chunk-b",
                    },
                }
            )
            await rag.entities_vdb.index_done_callback()
            await rag.relationships_vdb.index_done_callback()
        finally:
            await rag.finalize_storages()

        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            for ids, product, year, level, asset, region in [
                (NODES_A, "SPOT", "2024", "Secret", "BTC", "USA"),
                (NODES_B, "CONTRACT", "2025", "Public", "ETH", "JP"),
            ]:
                await connection.execute(
                    """
                    UPDATE lightrag_graph_nodes SET product=$1::text[], year=$2::text[],
                        security_level=$3::text[], asset=$4::text[], region=$5::text[]
                    WHERE id = ANY($6::text[])
                    """,
                    [product],
                    [year],
                    [level],
                    [asset],
                    [region],
                    ids,
                )
                await connection.execute(
                    """
                    UPDATE lightrag_graph_edges SET product=$1::text[], year=$2::text[],
                        security_level=$3::text[], asset=$4::text[], region=$5::text[]
                    WHERE src_id = ANY($6::text[])
                    """,
                    [product],
                    [year],
                    [level],
                    [asset],
                    [region],
                    ids,
                )
                await connection.execute(
                    """
                    UPDATE LIGHTRAG_VDB_ENTITY_test_embed_32d SET product=$1::text[],
                        year=$2::text[], security_level=$3::text[], asset=$4::text[],
                        region=$5::text[]
                    WHERE id = ANY($6::text[])
                    """,
                    [product],
                    [year],
                    [level],
                    [asset],
                    [region],
                    ids,
                )
                await connection.execute(
                    """
                    UPDATE LIGHTRAG_VDB_RELATION_test_embed_32d SET product=$1::text[],
                        year=$2::text[], security_level=$3::text[], asset=$4::text[],
                        region=$5::text[]
                    WHERE source_id = ANY($6::text[])
                    """,
                    [product],
                    [year],
                    [level],
                    [asset],
                    [region],
                    ids,
                )
        finally:
            await connection.close()

    asyncio.run(_prepare())


async def test_seed_gates_3a_3b(settings: Settings) -> None:
    rag = await make_rag(settings)
    try:
        with ScopeContext(scope_a()):
            entities = await rag.entities_vdb.query("A1", top_k=10)
            assert {row["entity_name"] for row in entities} == {"A1", "A2"}
            relations = await rag.relationships_vdb.query("A1", top_k=10)
            assert {(row["src_id"], row["tgt_id"]) for row in relations} == {("A1", "A2")}
        with ScopeContext(scope_b()):
            entities = await rag.entities_vdb.query("B1", top_k=10)
            assert {row["entity_name"] for row in entities} == {"B1", "B2"}
            relations = await rag.relationships_vdb.query("B1", top_k=10)
            assert {(row["src_id"], row["tgt_id"]) for row in relations} == {("B1", "B2")}
    finally:
        await rag.finalize_storages()


async def test_graph_batches_and_degree_same_predicate(settings: Settings) -> None:
    rag = await make_rag(settings)
    graph = rag.chunk_entity_relation_graph
    try:
        with ScopeContext(scope_a()):
            nodes = await graph.get_nodes_batch(["A1", "B1"])
            assert set(nodes) == {"A1"}
            edges = await graph.get_edges_batch(
                [{"src": "A1", "tgt": "A2"}, {"src": "B1", "tgt": "B2"}]
            )
            assert set(edges) == {("A1", "A2")}
            adjacency = await graph.get_nodes_edges_batch(["A1", "B1"])
            assert adjacency["A1"] == [("A1", "A2")]
            assert adjacency["B1"] == []
            degrees = await graph.node_degrees_batch(["A1", "B1"])
            assert degrees == {"A1": 1, "B1": 0}
            edge_degrees = await graph.edge_degrees_batch([("A1", "A2"), ("B1", "B2")])
            assert edge_degrees[("A1", "A2")] == 2
            assert edge_degrees[("B1", "B2")] == 0
        without_scope = await graph.get_nodes_batch(["A1", "B1"])
        assert set(without_scope) == {"A1", "B1"}
    finally:
        await rag.finalize_storages()


def test_degree_and_batch_sql_share_predicate() -> None:
    scope = scope_a()
    nodes_sql, _ = build_nodes_sql(scope)
    degrees_sql, _ = build_degrees_sql(scope)
    edges_sql, _ = build_edges_sql(scope)
    adjacency_sql, _ = build_nodes_edges_sql(scope)
    predicate = nodes_sql[nodes_sql.index("(product") :]
    assert predicate in degrees_sql
    assert predicate in adjacency_sql
    assert "product && $5::text[]" in edges_sql


async def test_global_branch_does_not_call_degree_local_does(settings: Settings) -> None:
    rag = await make_rag(settings)
    graph = rag.chunk_entity_relation_graph
    original = graph.node_degrees_batch
    calls = {"count": 0}

    async def spy(node_ids: list[str]) -> dict[str, int]:
        calls["count"] += 1
        return await original(node_ids)

    graph.node_degrees_batch = spy  # type: ignore[method-assign]
    try:
        with ScopeContext(scope_a()):
            global_result = await rag.aquery_data(
                "A1 entity",
                QueryParam(
                    mode="global",
                    top_k=10,
                    chunk_top_k=5,
                    hl_keywords=["A1"],
                    ll_keywords=["A1"],
                    enable_rerank=False,
                ),
            )
            assert global_result["status"] == "success"
            assert calls["count"] == 0
            local_result = await rag.aquery_data(
                "A1 entity",
                QueryParam(
                    mode="local",
                    top_k=10,
                    chunk_top_k=5,
                    hl_keywords=["A1"],
                    ll_keywords=["A1"],
                    enable_rerank=False,
                ),
            )
            assert local_result["status"] == "success"
            assert calls["count"] > 0
    finally:
        graph.node_degrees_batch = original  # type: ignore[method-assign]
        await rag.finalize_storages()
