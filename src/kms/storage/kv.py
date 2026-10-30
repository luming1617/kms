"""注入点 ①：KV 正文读取（图谱路唯一正文出口）。类名必须保持 PGKVStorage。"""

from __future__ import annotations

import json
from typing import Any, cast

from lightrag.kg.postgres_impl import SQL_TEMPLATES
from lightrag.kg.postgres_impl import PGKVStorage as _FrameworkPGKVStorage
from lightrag.namespace import NameSpace

from kms.scope import (
    FilterAST,
    current_metrics,
    current_scope,
    ensure_write_path,
    render_scope_sql,
)


def build_text_chunks_sql(ast: FilterAST, *, start_param: int = 3) -> tuple[str, list[Any]]:
    """在框架模板后追加 scope 谓词；KV 模板表名硬编码，不走 format。"""
    base = SQL_TEMPLATES["get_by_ids_text_chunks"].rstrip().rstrip(";")
    predicate, params = render_scope_sql(ast, start_param=start_param)
    if not predicate:
        raise ValueError("scope 谓词为空：授权维与有效期必须始终在场")
    return f"{base} AND {predicate}", params


def build_text_chunk_by_id_sql(ast: FilterAST, *, start_param: int = 3) -> tuple[str, list[Any]]:
    base = SQL_TEMPLATES["get_by_id_text_chunks"].rstrip().rstrip(";")
    predicate, params = render_scope_sql(ast, start_param=start_param)
    if not predicate:
        raise ValueError("scope 谓词为空：授权维与有效期必须始终在场")
    return f"{base} AND {predicate}", params


def _normalize_text_chunk_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    for key in ("llm_cache_list", "heading", "sidecar"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [] if key == "llm_cache_list" else {}
        if value is None:
            value = [] if key == "llm_cache_list" else {}
        row[key] = value
    return row


def reorder_to(ids: list[str], rows: list[dict[str, Any]] | None) -> list[dict[str, Any] | None]:
    """按入参顺序逐位对齐，缺失位填 None（铁律 2）。"""
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if row is None or row.get("id") is None:
            continue
        by_id[str(row["id"])] = row
    return [by_id.get(str(chunk_id)) for chunk_id in ids]


class PGKVStorage(_FrameworkPGKVStorage):
    async def get_by_id(self, id: str) -> dict[str, Any] | None:
        ast = current_scope()
        if ast is None or self.namespace != NameSpace.KV_STORE_TEXT_CHUNKS:
            return cast("dict[str, Any] | None", await super().get_by_id(id))
        sql, extra = build_text_chunk_by_id_sql(ast)
        response = await self.db.query(sql, params=[self.workspace, id, *extra], multirows=False)
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("text_chunks", 1, 1 if response else 0)
            metrics.l1_requested_chunk_ids += 1
            metrics.l1_rows_after += 1 if response else 0
            if response is None:
                metrics.l1_missing_chunk_ids += 1
        return _normalize_text_chunk_row(response) if isinstance(response, dict) else None

    async def get_by_ids(self, ids: list[str]) -> list[dict[str, Any] | None]:
        if not ids:
            return []
        ast = current_scope()
        if ast is None or self.namespace != NameSpace.KV_STORE_TEXT_CHUNKS:
            return cast("list[dict[str, Any] | None]", await super().get_by_ids(ids))
        sql, extra = build_text_chunks_sql(ast)
        rows = await self.db.query(sql, params=[self.workspace, ids, *extra], multirows=True)
        ordered = reorder_to(ids, rows if isinstance(rows, list) else [])
        ordered = [_normalize_text_chunk_row(row) for row in ordered]
        found = sum(1 for row in ordered if row is not None)
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("text_chunks", len(ids), found)
            metrics.l1_requested_chunk_ids += len(ids)
            metrics.l1_rows_after += found
            metrics.l1_missing_chunk_ids += len(ids) - found
        return ordered

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().delete(*args, **kwargs)

    async def drop(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().drop(*args, **kwargs)

    async def index_done_callback(self, *args: Any, **kwargs: Any) -> Any:
        # 查询收尾会 flush llm_response_cache；缓存不承载授权内容，放行。
        # 正文 namespace 的 flush 仍属写路径，必须 scope-free。
        if self.namespace == NameSpace.KV_STORE_TEXT_CHUNKS:
            ensure_write_path()
        return await super().index_done_callback(*args, **kwargs)
