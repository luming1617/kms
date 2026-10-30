"""注入点 ②（chunks 正文闸）与 ④（get_vectors_by_ids）；③a/③b 在 M4 接入。"""

from __future__ import annotations

from typing import Any, cast

from lightrag.kg.postgres_impl import SQL_TEMPLATES
from lightrag.kg.postgres_impl import PGVectorStorage as _FrameworkPGVectorStorage
from lightrag.namespace import NameSpace

from kms.scope import (
    SET_DIMS,
    FilterAST,
    current_metrics,
    current_scope,
    ensure_write_path,
    render_scope_sql,
)


def _vector_cast(storage: Any) -> str:
    if getattr(storage.db, "vector_index_type", None) == "HNSW_HALFVEC":
        return "halfvec"
    return "vector"


def build_vector_query_sql(
    namespace: str,
    table_name: str,
    ast: FilterAST,
    *,
    start_param: int = 5,
    vector_cast: str = "vector",
) -> tuple[str, list[Any]]:
    template = SQL_TEMPLATES[namespace].format(table_name=table_name, vector_cast=vector_cast)
    if namespace == NameSpace.VECTOR_STORE_CHUNKS:
        predicate, params = render_scope_sql(ast, start_param=start_param)
    else:
        predicate, params = render_scope_sql(
            ast,
            start_param=start_param,
            dims=SET_DIMS,
            include_publish_state=False,
            include_validity=False,
        )
    if "ORDER BY" not in template:
        raise ValueError(f"向量模板缺少 ORDER BY：{namespace}")
    head, tail = template.split("ORDER BY", 1)
    return f"{head.rstrip()}\n  AND {predicate}\nORDER BY{tail}", params


def build_vectors_by_ids_sql(
    table_name: str, ast: FilterAST, *, for_chunks: bool, start_param: int = 3
) -> tuple[str, list[Any]]:
    if for_chunks:
        predicate, params = render_scope_sql(ast, start_param=start_param)
    else:
        predicate, params = render_scope_sql(
            ast,
            start_param=start_param,
            dims=SET_DIMS,
            include_publish_state=False,
            include_validity=False,
        )
    sql = f"SELECT id FROM {table_name} WHERE workspace = $1 AND id = ANY($2) AND {predicate}"
    return sql, params


class PGVectorStorage(_FrameworkPGVectorStorage):
    def _scope_active(self) -> FilterAST | None:
        ast = current_scope()
        if ast is None:
            return None
        if self.namespace not in (
            NameSpace.VECTOR_STORE_CHUNKS,
            NameSpace.VECTOR_STORE_ENTITIES,
            NameSpace.VECTOR_STORE_RELATIONSHIPS,
        ):
            return None
        return ast

    async def query(
        self, query: str, top_k: int, query_embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        ast = self._scope_active()
        if ast is None:
            return cast(
                "list[dict[str, Any]]",
                await super().query(query, top_k, query_embedding),
            )
        if self.namespace not in (
            NameSpace.VECTOR_STORE_CHUNKS,
            NameSpace.VECTOR_STORE_ENTITIES,
            NameSpace.VECTOR_STORE_RELATIONSHIPS,
        ):
            return cast(
                "list[dict[str, Any]]",
                await super().query(query, top_k, query_embedding),
            )
        if query_embedding is not None:
            embedding = query_embedding
        else:
            embeddings = await self.embedding_func([query], context="query")
            embedding = embeddings[0]
        sql, extra = build_vector_query_sql(
            self.namespace, self.table_name, ast, vector_cast=_vector_cast(self)
        )
        rows = await self.db.query(
            sql,
            params=[
                self.workspace,
                1 - self.cosine_better_than_threshold,
                top_k,
                embedding,
                *extra,
            ],
            multirows=True,
        )
        result_rows = rows if isinstance(rows, list) else []
        metrics = current_metrics()
        if metrics is not None:
            # 分母是本次请求的候选窗口（top_k）；实际扫描集由 hnsw.ef_search 决定，
            # 不额外发一条无谓词查询，避免把监控成本转嫁到查询延迟（§12.1）。
            metrics.note_l1(self.namespace, top_k, len(result_rows))
            metrics.l1_rows_after += len(result_rows)
        return cast("list[dict[str, Any]]", result_rows)

    async def get_vectors_by_ids(self, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        ast = self._scope_active()
        if ast is None or self.namespace != NameSpace.VECTOR_STORE_CHUNKS:
            return cast("dict[str, list[float]]", await super().get_vectors_by_ids(ids))
        # 先走框架分支（含 pending 缓冲），再按 SQL 谓词裁切。
        # pending 行没有投影 scope，无法证明授权，按 fail-closed 丢弃并计数（§4.1 ④）。
        vectors = cast("dict[str, list[float]]", await super().get_vectors_by_ids(ids))
        if not vectors:
            return {}
        sql, extra = build_vectors_by_ids_sql(self.table_name, ast, for_chunks=True, start_param=3)
        rows = await self.db.query(sql, params=[self.workspace, ids, *extra], multirows=True)
        allowed = {str(row["id"]) for row in rows} if isinstance(rows, list) else set()
        result = {chunk_id: vector for chunk_id, vector in vectors.items() if chunk_id in allowed}
        pending = getattr(self, "_pending_vector_docs", {}) or {}
        dropped_pending = [
            chunk_id for chunk_id in vectors if chunk_id not in allowed and chunk_id in pending
        ]
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("chunk_vectors", len(ids), len(result))
            metrics.l1_pending_dropped += len(dropped_pending)
            metrics.l1_requested_chunk_ids += len(ids)
            metrics.l1_rows_after += len(result)
            metrics.l1_missing_chunk_ids += len(ids) - len(result)
        return result

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().delete(*args, **kwargs)

    async def delete_entity(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().delete_entity(*args, **kwargs)

    async def delete_entity_relation(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().delete_entity_relation(*args, **kwargs)

    async def drop(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().drop(*args, **kwargs)

    async def index_done_callback(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().index_done_callback(*args, **kwargs)

    async def _flush_pending_vector_ops(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super()._flush_pending_vector_ops(*args, **kwargs)

    async def drop_pending_index_ops(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().drop_pending_index_ops(*args, **kwargs)
