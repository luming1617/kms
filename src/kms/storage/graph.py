"""注入点 ⑤⑥⑦⑧⑨：一跳扩展与 degree；degree 必须与批查同谓词。"""

from __future__ import annotations

from typing import Any, cast

from lightrag.kg.pgtable_impl import PGTableGraphStorage as _FrameworkPGTableGraphStorage

from kms.scope import (
    SET_DIMS,
    FilterAST,
    current_metrics,
    current_scope,
    ensure_write_path,
    render_scope_sql,
)


def _candidate_predicate(ast: FilterAST, start_param: int) -> tuple[str, list[Any]]:
    predicate, params = render_scope_sql(
        ast,
        start_param=start_param,
        dims=SET_DIMS,
        include_publish_state=False,
        include_validity=False,
    )
    if not predicate:
        raise ValueError("候选表谓词为空")
    return predicate, params


def build_nodes_sql(ast: FilterAST) -> tuple[str, list[Any]]:
    predicate, params = _candidate_predicate(ast, 4)
    sql = (
        "SELECT id, properties FROM lightrag_graph_nodes "
        "WHERE workspace=$1 AND namespace=$2 AND id=ANY($3) "
        f"AND {predicate}"
    )
    return sql, params


def build_edges_sql(ast: FilterAST) -> tuple[str, list[Any]]:
    predicate, params = _candidate_predicate(ast, 5)
    sql = (
        "SELECT src_id, tgt_id, properties FROM lightrag_graph_edges "
        "WHERE workspace=$1 AND namespace=$2 "
        "AND (src_id, tgt_id) IN (SELECT * FROM unnest($3::text[], $4::text[])) "
        f"AND {predicate}"
    )
    return sql, params


def build_nodes_edges_sql(ast: FilterAST) -> tuple[str, list[Any]]:
    predicate, params = _candidate_predicate(ast, 4)
    sql = (
        "SELECT src_id, tgt_id FROM lightrag_graph_edges "
        "WHERE workspace=$1 AND namespace=$2 AND src_id=ANY($3) "
        f"AND {predicate} "
        "UNION "
        "SELECT src_id, tgt_id FROM lightrag_graph_edges "
        "WHERE workspace=$1 AND namespace=$2 AND tgt_id=ANY($3) "
        f"AND {predicate}"
    )
    return sql, params


def build_degrees_sql(ast: FilterAST) -> tuple[str, list[Any]]:
    predicate, params = _candidate_predicate(ast, 4)
    sql = (
        "SELECT id, COUNT(*) AS degree FROM ("
        "SELECT src_id AS id FROM lightrag_graph_edges "
        "WHERE workspace=$1 AND namespace=$2 AND src_id=ANY($3) "
        f"AND {predicate} "
        "UNION ALL "
        "SELECT tgt_id AS id FROM lightrag_graph_edges "
        "WHERE workspace=$1 AND namespace=$2 AND tgt_id=ANY($3) "
        f"AND {predicate}"
        ") sub GROUP BY id"
    )
    return sql, params


class PGTableGraphStorage(_FrameworkPGTableGraphStorage):
    async def get_nodes_batch(self, node_ids: list[str]) -> dict[str, dict]:
        if not node_ids:
            return {}
        ast = current_scope()
        if ast is None:
            return cast("dict[str, dict]", await super().get_nodes_batch(node_ids))
        sql, extra = build_nodes_sql(ast)
        rows = await self._fetch(sql, self.workspace, self.namespace, node_ids, *extra)
        result = {row["id"]: self._node_props(row["id"], row["properties"]) for row in rows}
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("graph_nodes", len(node_ids), len(result))
            metrics.l1_rows_before += len(node_ids)
            metrics.l1_rows_after += len(result)
        return result

    async def get_edges_batch(self, pairs: list[dict[str, str]]) -> dict[tuple[str, str], dict]:
        if not pairs:
            return {}
        ast = current_scope()
        if ast is None:
            return cast("dict[tuple[str, str], dict]", await super().get_edges_batch(pairs))
        canonical = [(min(p["src"], p["tgt"]), max(p["src"], p["tgt"])) for p in pairs]
        srcs = [item[0] for item in canonical]
        tgts = [item[1] for item in canonical]
        sql, extra = build_edges_sql(ast)
        rows = await self._fetch(sql, self.workspace, self.namespace, srcs, tgts, *extra)
        canonical_props = {
            (row["src_id"], row["tgt_id"]): self._json_loads(row["properties"]) for row in rows
        }
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("graph_edges", len(pairs), len(canonical_props))
            metrics.l1_rows_before += len(pairs)
            metrics.l1_rows_after += len(canonical_props)
        return {
            (p["src"], p["tgt"]): canonical_props[c]
            for p, c in zip(pairs, canonical, strict=False)
            if c in canonical_props
        }

    async def get_nodes_edges_batch(self, node_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
        if not node_ids:
            return {}
        ast = current_scope()
        if ast is None:
            return cast(
                "dict[str, list[tuple[str, str]]]", await super().get_nodes_edges_batch(node_ids)
            )
        sql, extra = build_nodes_edges_sql(ast)
        rows = await self._fetch(sql, self.workspace, self.namespace, node_ids, *extra)
        result: dict[str, list[tuple[str, str]]] = {node_id: [] for node_id in node_ids}
        for row in rows:
            src, tgt = row["src_id"], row["tgt_id"]
            if src in result:
                result[src].append((src, tgt))
            if tgt in result and tgt != src:
                result[tgt].append((tgt, src))
        for edges in result.values():
            edges.sort(key=lambda edge: edge[1])
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("graph_adjacency", len(node_ids), len(rows))
            metrics.l1_rows_before += len(node_ids)
            metrics.l1_rows_after += len(rows)
        return result

    async def node_degrees_batch(self, node_ids: list[str]) -> dict[str, int]:
        if not node_ids:
            return {}
        ast = current_scope()
        if ast is None:
            return cast("dict[str, int]", await super().node_degrees_batch(node_ids))
        sql, extra = build_degrees_sql(ast)
        rows = await self._fetch(sql, self.workspace, self.namespace, node_ids, *extra)
        result = {node_id: 0 for node_id in node_ids}
        for row in rows:
            result[row["id"]] = int(row["degree"])
        metrics = current_metrics()
        if metrics is not None:
            metrics.note_l1("graph_degrees", len(node_ids), len(rows))
        return result

    async def upsert_node(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert_node(*args, **kwargs)

    async def delete_node(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().delete_node(*args, **kwargs)

    async def upsert_edge(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert_edge(*args, **kwargs)

    async def upsert_nodes_batch(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert_nodes_batch(*args, **kwargs)

    async def upsert_edges_batch(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().upsert_edges_batch(*args, **kwargs)

    async def remove_nodes(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().remove_nodes(*args, **kwargs)

    async def remove_edges(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().remove_edges(*args, **kwargs)

    async def drop(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().drop(*args, **kwargs)

    async def index_done_callback(self, *args: Any, **kwargs: Any) -> Any:
        ensure_write_path()
        return await super().index_done_callback(*args, **kwargs)
