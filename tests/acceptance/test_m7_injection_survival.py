"""M7：10 个注入点存活性 + 写路径护栏 + 谓词/SQL 断言（§12.3）。"""

from __future__ import annotations

import pytest
from probes.probe_signatures import collect

from src.kms.scope import ScopeContext, SessionScope
from src.kms.storage.graph import PGTableGraphStorage, build_degrees_sql, build_nodes_sql
from src.kms.storage.kv import PGKVStorage, build_text_chunks_sql
from src.kms.storage.vector import PGVectorStorage, build_vector_query_sql

pytestmark = pytest.mark.m7

OVERRIDDEN = {
    PGKVStorage: ("get_by_id", "get_by_ids", "upsert", "delete", "drop"),
    PGVectorStorage: (
        "query",
        "get_vectors_by_ids",
        "upsert",
        "delete",
        "drop",
        "index_done_callback",
        "_flush_pending_vector_ops",
    ),
    PGTableGraphStorage: (
        "get_nodes_batch",
        "get_edges_batch",
        "get_nodes_edges_batch",
        "node_degrees_batch",
        "upsert_node",
        "upsert_edge",
        "remove_nodes",
        "remove_edges",
        "drop",
    ),
}


def test_nine_methods_ten_points_still_present() -> None:
    payload = collect()
    assert payload["passed"]
    assert payload["injection_points"]["count"] == 10
    assert len(payload["methods"]) == 9


def test_scoped_classes_override_injection_methods() -> None:
    for cls, methods in OVERRIDDEN.items():
        for method in methods:
            assert method in cls.__dict__, f"{cls.__name__}.{method} 未覆盖"


def test_predicates_present_in_all_scoped_sql() -> None:
    ast = SessionScope(
        language="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    ).to_ast()
    kv_sql, kv_params = build_text_chunks_sql(ast)
    vector_sql, vector_params = build_vector_query_sql(
        "chunks", "LIGHTRAG_VDB_CHUNKS_test_embed_32d", ast
    )
    nodes_sql, node_params = build_nodes_sql(ast)
    degrees_sql, degree_params = build_degrees_sql(ast)
    for sql in (kv_sql, vector_sql, nodes_sql, degrees_sql):
        assert "product = '{}'::text[]" in sql
        assert "security_level && " in sql
    assert len(kv_params) == 7 and len(node_params) == len(degree_params) == 5
    assert len(vector_params) == 7


async def test_write_path_guard_covers_all_scoped_classes() -> None:
    from lightrag.namespace import NameSpace

    from src.kms.scope import ScopeContaminationError

    ast = SessionScope(
        language="zh-CN", product=("SPOT",), year=("2024",), security_level="Secret"
    ).to_ast()
    kv = object.__new__(PGKVStorage)
    kv.namespace = NameSpace.KV_STORE_TEXT_CHUNKS
    vector = object.__new__(PGVectorStorage)
    graph = object.__new__(PGTableGraphStorage)
    with ScopeContext(ast):
        for instance, methods in (
            (kv, ("upsert", "delete", "drop", "index_done_callback")),
            (
                vector,
                ("upsert", "delete", "drop", "index_done_callback", "_flush_pending_vector_ops"),
            ),
            (graph, ("upsert_node", "upsert_edge", "remove_nodes", "remove_edges", "drop")),
        ):
            for method in methods:
                with pytest.raises(ScopeContaminationError):
                    await getattr(instance, method)()
