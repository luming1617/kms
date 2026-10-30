"""M0-2：复跑 9 个方法 / 10 个注入点签名与 SQL 模板取证（附录 B）。"""

from __future__ import annotations

import inspect

from lightrag.kg import STORAGES
from lightrag.kg import factory as storage_factory
from lightrag.kg.pgtable_impl import PGTableGraphStorage
from lightrag.kg.postgres_impl import (
    SQL_TEMPLATES,
    ClientManager,
    PGKVStorage,
    PGVectorStorage,
)
from lightrag.namespace import NameSpace
from probes.common import print_summary, write_report

EXPECTED_METHODS = {
    "PGKVStorage.get_by_id": (PGKVStorage, "get_by_id", ["id"]),
    "PGKVStorage.get_by_ids": (PGKVStorage, "get_by_ids", ["ids"]),
    "PGVectorStorage.query": (PGVectorStorage, "query", ["query", "top_k", "query_embedding"]),
    "PGVectorStorage.get_vectors_by_ids": (PGVectorStorage, "get_vectors_by_ids", ["ids"]),
    "PGTableGraphStorage.get_nodes_batch": (PGTableGraphStorage, "get_nodes_batch", ["node_ids"]),
    "PGTableGraphStorage.get_edges_batch": (PGTableGraphStorage, "get_edges_batch", ["pairs"]),
    "PGTableGraphStorage.get_nodes_edges_batch": (
        PGTableGraphStorage,
        "get_nodes_edges_batch",
        ["node_ids"],
    ),
    "PGTableGraphStorage.node_degrees_batch": (
        PGTableGraphStorage,
        "node_degrees_batch",
        ["node_ids"],
    ),
    "PGTableGraphStorage.edge_degrees_batch": (
        PGTableGraphStorage,
        "edge_degrees_batch",
        ["edge_pairs"],
    ),
}

WRITE_METHODS = {
    "PGKVStorage": (PGKVStorage, ["upsert", "delete", "drop"]),
    "PGVectorStorage": (
        PGVectorStorage,
        ["upsert", "delete", "drop", "index_done_callback", "_flush_pending_vector_ops"],
    ),
    "PGTableGraphStorage": (
        PGTableGraphStorage,
        [
            "upsert_node",
            "upsert_edge",
            "remove_nodes",
            "remove_edges",
            "upsert_nodes_batch",
            "upsert_edges_batch",
            "drop",
        ],
    ),
}


def _source(cls: type, name: str) -> str:
    try:
        return inspect.getsource(getattr(cls, name))
    except OSError:
        return ""


def collect() -> dict:
    methods = []
    all_ok = True
    for label, (cls, name, expected_params) in EXPECTED_METHODS.items():
        sig = inspect.signature(getattr(cls, name))
        params = [p for p in sig.parameters if p != "self"]
        ok = params == expected_params
        all_ok = all_ok and ok
        methods.append(
            {
                "label": label,
                "defined_in": f"{cls.__module__}.{cls.__name__}",
                "signature": f"{name}{sig}",
                "parameters": params,
                "expected": expected_params,
                "ok": ok,
                "line": inspect.getsourcelines(getattr(cls, name))[1],
            }
        )

    kv_template = SQL_TEMPLATES["get_by_ids_text_chunks"]
    chunk_template = SQL_TEMPLATES["chunks"]
    entity_template = SQL_TEMPLATES["entities"]
    relation_template = SQL_TEMPLATES["relationships"]

    templates = {
        "kv_text_chunks": {
            "hardcoded_table": "FROM LIGHTRAG_DOC_CHUNKS" in kv_template,
            "params_workspace_ids_only": "workspace=$1" in kv_template
            and "id = ANY($2)" in kv_template,
            "selects_content": "content" in kv_template,
        },
        "vector_chunks": {
            "selects_content": "content" in chunk_template,
            "workspace_predicate": "workspace = $1" in chunk_template,
            "embedding_param": "$4" in chunk_template,
            "order_before_limit": "ORDER BY" in chunk_template,
        },
        "vector_entities": {
            "workspace_predicate": "workspace = $1" in entity_template,
            "selects_no_descriptive_content": "SELECT content" not in entity_template,
        },
        "vector_relations": {
            "workspace_predicate": "workspace = $1" in relation_template,
            "selects_no_descriptive_content": "SELECT content" not in relation_template,
        },
    }

    graph_reads = {}
    for name in [
        "get_nodes_batch",
        "get_edges_batch",
        "get_nodes_edges_batch",
        "node_degrees_batch",
    ]:
        source = _source(PGTableGraphStorage, name)
        graph_reads[name] = {
            "has_workspace_predicate": "workspace=$1" in source,
            "has_namespace_predicate": "namespace=$2" in source,
            "has_scope_columns": any(dim in source for dim in ("product", "asset", "region")),
        }

    vector_query_source = _source(PGVectorStorage, "query")
    vector_get_vectors_source = _source(PGVectorStorage, "get_vectors_by_ids")

    write_methods = {
        label: {
            "methods": names,
            "present": all(hasattr(cls, n) for n in names),
        }
        for label, (cls, names) in WRITE_METHODS.items()
    }

    client_config = ClientManager.get_config(vector_storage="PGVectorStorage")
    client_config_subclass_name = ClientManager.get_config(vector_storage="ScopedPGVectorStorage")
    backend_checks = {
        "storages_registered": {
            key: STORAGES.get(key)
            for key in [
                "PGKVStorage",
                "PGVectorStorage",
                "PGTableGraphStorage",
                "PGDocStatusStorage",
            ]
        },
        "factory_loads_class_by_same_name": "getattr(module, storage_name)"
        in inspect.getsource(storage_factory.get_storage_class),
        "vector_signature_with_framework_name": {
            "enable_vector": client_config["enable_vector"],
            "vector_storage_key": "PGVectorStorage",
        },
        "vector_signature_with_subclass_name": {
            "enable_vector": client_config_subclass_name["enable_vector"],
            "note": "P0：配置成子类名会让 enable_vector=False，因此只能重指向 STORAGES 键",
        },
    }

    injection_points = {
        "count": 10,
        "detail": {
            "①": "PGKVStorage.get_by_ids / get_by_id（namespace=text_chunks）",
            "②": "PGVectorStorage.query → chunks",
            "③a": "PGVectorStorage.query → entities",
            "③b": "PGVectorStorage.query → relationships",
            "④": "PGVectorStorage.get_vectors_by_ids",
            "⑤": "PGTableGraphStorage.get_nodes_batch",
            "⑥": "PGTableGraphStorage.get_edges_batch",
            "⑦": "PGTableGraphStorage.get_nodes_edges_batch",
            "⑧": "PGTableGraphStorage.node_degrees_batch",
            "⑨": "PGTableGraphStorage.edge_degrees_batch",
        },
        "namespace_values": {
            "chunks": NameSpace.VECTOR_STORE_CHUNKS,
            "entities": NameSpace.VECTOR_STORE_ENTITIES,
            "relationships": NameSpace.VECTOR_STORE_RELATIONSHIPS,
            "text_chunks": NameSpace.KV_STORE_TEXT_CHUNKS,
        },
    }

    payload = {
        "probe": "signatures",
        "lightrag_version": "1.5.7",
        "methods": methods,
        "templates": templates,
        "graph_reads": graph_reads,
        "get_vectors_by_ids_branches": {
            "pending_buffer_branch": "_pending_vector_docs" in vector_get_vectors_source,
            "sql_branch": "content_vector" in vector_get_vectors_source
            or "SELECT" in vector_get_vectors_source.upper(),
            "query_has_template_params": "closer_than_threshold" in vector_query_source,
        },
        "write_methods": write_methods,
        "backends": backend_checks,
        "injection_points": injection_points,
        "passed": all_ok
        and all(templates["kv_text_chunks"].values())
        and all(templates["vector_chunks"].values())
        and all(v["present"] for v in write_methods.values())
        and backend_checks["vector_signature_with_framework_name"]["enable_vector"]
        and not backend_checks["vector_signature_with_subclass_name"]["enable_vector"]
        and not any(v["has_scope_columns"] for v in graph_reads.values()),
    }
    return payload


def main() -> None:
    payload = collect()
    write_report("02-signatures", payload)
    print_summary(payload)


if __name__ == "__main__":
    main()
