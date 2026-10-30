"""四后端显式配置与 STORAGES 重指向（类名不变，§13 P0）。"""

from __future__ import annotations

import os
from typing import Any

from lightrag import LightRAG
from lightrag.kg import STORAGES
from lightrag.kg.postgres_impl import (
    PG_MAX_IDENTIFIER_LENGTH,
    ClientManager,
    namespace_to_table_name,
)
from lightrag.namespace import NameSpace
from lightrag.utils import EmbeddingFunc

from kms.config import Settings

SCOPED_BACKENDS = {
    "PGKVStorage": "kms.storage.kv",
    "PGVectorStorage": "kms.storage.vector",
    "PGTableGraphStorage": "kms.storage.graph",
}

_process_vector_signature: dict[str, Any] | None = None


class BackendWiringError(RuntimeError):
    """后端配置不满足方案约束（启动即失败）。"""


def install_scoped_backends() -> dict[str, str]:
    for name, module in SCOPED_BACKENDS.items():
        STORAGES[name] = module
    return dict(SCOPED_BACKENDS)


def _table_names(embedding_func: EmbeddingFunc) -> dict[str, str]:
    from lightrag.base import BaseVectorStorage

    class _Stub:
        pass

    stub = _Stub()
    stub.embedding_func = embedding_func  # type: ignore[attr-defined]
    suffix = BaseVectorStorage._generate_collection_suffix(stub)  # type: ignore[arg-type]
    if not suffix:
        raise BackendWiringError("embedding_func.model_name 为空：向量表会回落成裸基表（§13）")
    names = {}
    for label, namespace in {
        "chunks": NameSpace.VECTOR_STORE_CHUNKS,
        "entities": NameSpace.VECTOR_STORE_ENTITIES,
        "relationships": NameSpace.VECTOR_STORE_RELATIONSHIPS,
    }.items():
        names[label] = f"{namespace_to_table_name(namespace)}_{suffix}"
    longest = max(len(name) for name in names.values())
    if longest > PG_MAX_IDENTIFIER_LENGTH:
        raise BackendWiringError(f"向量表名超过 {PG_MAX_IDENTIFIER_LENGTH} 字符：{names}")
    return names


def build_lightrag(
    settings: Settings,
    *,
    workspace: str,
    embedding_func: EmbeddingFunc,
    llm_model_func: Any,
    working_dir: str | None = None,
    cosine_threshold: float = 0.2,
    extra: dict[str, Any] | None = None,
) -> LightRAG:
    import tempfile

    settings.validate_startup()
    os.environ.update(settings.postgres.lightrag_env())
    install_scoped_backends()
    _table_names(embedding_func)
    constructor_kwargs = {
        "min_rerank_score": settings.retrieval.min_rerank_score,
        **(extra or {}),
    }
    rag = LightRAG(
        working_dir=working_dir or os.path.join(tempfile.gettempdir(), "kms_workdir"),
        llm_model_func=llm_model_func,
        embedding_func=embedding_func,
        kv_storage="PGKVStorage",
        vector_storage="PGVectorStorage",
        graph_storage="PGTableGraphStorage",
        doc_status_storage="PGDocStatusStorage",
        workspace=workspace,
        vector_db_storage_cls_kwargs={"cosine_better_than_threshold": cosine_threshold},
        enable_llm_cache=False,
        enable_llm_cache_for_entity_extract=True,
        **constructor_kwargs,
    )
    assert_backend_wiring(rag)
    return rag


def assert_backend_wiring(rag: LightRAG) -> dict[str, Any]:
    global _process_vector_signature

    if os.environ.get("POSTGRES_WORKSPACE"):
        raise BackendWiringError("POSTGRES_WORKSPACE 必须为空（§3.1/§13）")

    config = ClientManager.get_config(vector_storage="PGVectorStorage")
    if config["enable_vector"] is not True:
        raise BackendWiringError("vector_signature.enable_vector 必须为 True")

    expected = {
        "kv_storage": ("PGKVStorage", "kms.storage.kv"),
        "vector_storage": ("PGVectorStorage", "kms.storage.vector"),
        "graph_storage": ("PGTableGraphStorage", "kms.storage.graph"),
    }
    configured = {
        "kv_storage": rag.kv_storage,
        "vector_storage": rag.vector_storage,
        "graph_storage": rag.graph_storage,
    }
    for label, value in configured.items():
        if not isinstance(value, str) or value not in SCOPED_BACKENDS:
            raise BackendWiringError(f"{label} 配置必须是框架原生后端名：{value!r}")
    storages = {
        "kv_storage": rag.text_chunks,
        "vector_storage": rag.chunks_vdb,
        "graph_storage": rag.chunk_entity_relation_graph,
    }
    for label, (class_name, module_name) in expected.items():
        storage = storages[label]
        if type(storage).__name__ != class_name or type(storage).__module__ != module_name:
            raise BackendWiringError(
                f"{label} 未重指向到自有子类：{type(storage).__module__}.{type(storage).__name__}"
            )

    doc_status = rag.doc_status
    if (
        type(doc_status).__name__ != "PGDocStatusStorage"
        or "postgres_impl" not in type(doc_status).__module__
    ):
        raise BackendWiringError(f"doc_status_storage 不是 PG 实现：{type(doc_status)}")

    # 签名只覆盖池级/索引级配置；embedding 模型允许按语言差异化（§3.1）。
    signature = {
        "vector_storage": "PGVectorStorage",
        "enable_vector": True,
        "vector_index_type": config["vector_index_type"],
        "hnsw_m": config["hnsw_m"],
        "hnsw_ef": config["hnsw_ef"],
    }
    if _process_vector_signature is not None and _process_vector_signature != signature:
        raise BackendWiringError(
            f"同进程 vector_signature 不一致：{_process_vector_signature} vs {signature}"
        )
    _process_vector_signature = signature
    return {
        "storages": {k: f"{type(v).__module__}.{type(v).__name__}" for k, v in storages.items()},
        "doc_status": f"{type(doc_status).__module__}.{type(doc_status).__name__}",
        "vector_signature": signature,
        "embedding": {
            "model": getattr(rag.embedding_func, "model_name", None),
            "dim": getattr(rag.embedding_func, "embedding_dim", None),
        },
    }
