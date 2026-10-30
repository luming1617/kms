"""§八 入库与发布流水线：发布闸 → 摄取 → 版本快照 → 回填 → 对账。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from lightrag import LightRAG

from kms.authority.models import CardDraft
from kms.authority.projection import resolve_projection_tables
from kms.authority.repository import (
    add_card_version,
    compute_answer_hash,
    create_card,
    enqueue_conflict,
    get_card,
    get_current_version,
    set_publish_state,
)
from kms.authority.schema import apply_authority_schema
from kms.authority.validation import (
    Conflict,
    ValidationIssue,
    find_conflicts,
    validate_draft,
)
from kms.config import Settings
from kms.db import Database
from kms.publish.backfill import (
    ChunkScopeRow,
    assert_same_scope,
    backfill_chunk_tables,
    merge_chunk_filter,
    read_doc_chunk_ids,
)
from kms.publish.freeze import is_frozen
from kms.publish.graph_scope import recompute_candidate_scopes
from kms.publish.ids import apply_knowledge_prefix
from kms.publish.reconcile import LanguageReport, reconcile_card
from kms.publish.render import render_content
from kms.scope import FilterAST
from kms.workspace import WorkspaceRecord, WorkspaceRegistry

T = Any


class PublishError(RuntimeError):
    pass


class PublishRejected(PublishError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("; ".join(f"{issue.code}:{issue.message}" for issue in issues))
        self.issues = issues


class PublishConflictError(PublishError):
    def __init__(self, conflicts: list[Conflict]) -> None:
        super().__init__("冲突卡已进入人工队列：" + ",".join(c.kid for c in conflicts))
        self.conflicts = conflicts


class PublishFrozenError(PublishError):
    pass


class PublishBusy(PublishError):
    pass


class PublishFailed(PublishError):
    pass


class PublishReconcileError(PublishError):
    def __init__(self, report: LanguageReport) -> None:
        super().__init__(f"发布后对账未平：{report.language}")
        self.report = report


@dataclass(frozen=True, slots=True)
class PublishResult:
    status: str
    kid: str
    version: int
    chunk_count: int
    corpus_generation: int
    reconcile_ok: bool


class PublishPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        registry: WorkspaceRegistry,
        rag: LightRAG,
        workspace: WorkspaceRecord,
    ) -> None:
        self._settings = settings
        self._db = database
        self._registry = registry
        self._rag = rag
        self._workspace = workspace
        self._tables = resolve_projection_tables(workspace.embedding_model, workspace.embedding_dim)
        if rag.workspace != workspace.lang:
            raise PublishFailed(
                f"RAG workspace={rag.workspace!r} 与卡语言={workspace.lang!r} 不一致"
            )
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, language: str) -> asyncio.Lock:
        lock = self._locks.get(language)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[language] = lock
        return lock

    async def _retry(self, operation: Callable[[], Awaitable[T]], *, attempts: int = 3) -> T:
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return await operation()
            except Exception as exc:
                last = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (2**attempt))
        raise PublishFailed(f"发布重试失败：{last}") from last

    @asynccontextmanager
    async def _advisory_lock(self, language: str):
        """跨进程发布互斥；连接关闭即释放，崩溃不会留下死锁。"""
        key = f"kms:publish:{language}"
        deadline = time.monotonic() + self._settings.publish_lock_timeout_seconds
        async with self._db.acquire() as connection:
            while True:
                acquired = await connection.fetchval(
                    "SELECT pg_try_advisory_lock(hashtext($1)::bigint)", key
                )
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise PublishBusy(f"语言 {language} 发布锁等待超时")
                await asyncio.sleep(0.2)
            try:
                yield
            finally:
                await connection.execute("SELECT pg_advisory_unlock(hashtext($1)::bigint)", key)

    async def _assert_not_frozen(self, language: str) -> None:
        async with self._db.acquire() as connection:
            if await is_frozen(connection, language):
                raise PublishFrozenError(f"语言 {language} 发布队列已冻结（§12.2）")

    async def _unchanged(self, draft: CardDraft) -> PublishResult | None:
        async with self._db.acquire() as connection:
            card = await get_card(connection, draft.kid)
            if card is None or card["publish_state"] != "PUBLISHED":
                return None
            current = await get_current_version(connection, draft.kid)
            if current is None:
                return None
            draft_hash = await compute_answer_hash(connection, draft.content)
            if draft_hash != current["answer_hash"]:
                return None
            for dim in ("product", "year", "security_level", "asset", "region"):
                if sorted(card[dim]) != sorted(draft.array(dim)):
                    return None
            if card["valid_from"] != draft.valid_from or card["valid_to"] != draft.valid_to:
                return None
            chunk_ids = await read_doc_chunk_ids(
                connection, workspace=draft.language, doc_id=draft.kid
            )
            if not chunk_ids:
                return None
            version = int(card["current_version"])
            cf_rows = await connection.fetch(
                """
                SELECT chunk_id, product, year, security_level, asset, region,
                       publish_state, valid_from, valid_to
                FROM chunk_filter WHERE kid = $1 AND kid_version = $2
                """,
                draft.kid,
                version,
            )
            if len(cf_rows) != len(chunk_ids):
                return None
            expected = {
                "product": sorted(draft.product),
                "year": sorted(draft.year),
                "security_level": sorted(draft.security_level),
                "asset": sorted(draft.asset),
                "region": sorted(draft.region),
                "publish_state": draft.publish_state,
                "valid_from": draft.valid_from,
                "valid_to": draft.valid_to,
            }
            for row in cf_rows:
                if any(
                    sorted(row[dim] or []) != values
                    for dim, values in expected.items()
                    if dim in {"product", "year", "security_level", "asset", "region"}
                ):
                    return None
                if row["publish_state"] != expected["publish_state"]:
                    return None
                if (
                    row["valid_from"] != expected["valid_from"]
                    or row["valid_to"] != expected["valid_to"]
                ):
                    return None
            # 两张投影表必须与权威快照逐行一致；缺失/漂移都按“需要重发”处理。
            differences = await assert_same_scope(
                connection,
                tables=self._tables,
                workspace=draft.language,
                chunk_ids=chunk_ids,
            )
            if differences:
                return None
            pending = await connection.fetchval(
                f"SELECT count(*) FROM {self._tables['doc_chunks']} "
                "WHERE workspace = $1 AND id = ANY($2::text[]) AND publish_state = 'PENDING'",
                draft.language,
                chunk_ids,
            )
            if pending:
                return None
            return PublishResult(
                status="unchanged",
                kid=draft.kid,
                version=version,
                chunk_count=len(chunk_ids),
                corpus_generation=0,
                reconcile_ok=True,
            )

    async def _ingest(self, draft: CardDraft, *, vector_only: bool) -> None:
        rendered = apply_knowledge_prefix(draft.kid, render_content(draft.content))
        if vector_only:
            await self._rag.apipeline_enqueue_documents(
                [rendered],
                ids=[draft.kid],
                file_paths=[draft.kid],
                process_options="F!",
            )
            await self._rag.apipeline_process_enqueue_documents()
        else:
            await self._rag.ainsert(rendered, ids=[draft.kid], file_paths=[draft.kid])

    def _chunk_rows(
        self, draft: CardDraft, chunk_ids: list[str], version: int
    ) -> list[ChunkScopeRow]:
        ast = FilterAST(
            product=tuple(draft.product),
            year=tuple(draft.year),
            security_level=tuple(draft.security_level),
            asset=tuple(draft.asset),
            region=tuple(draft.region),
            publish_state=draft.publish_state,
            valid_at=draft.valid_from,
        )
        return [
            ChunkScopeRow.from_ast(
                chunk_id=chunk_id,
                kid=draft.kid,
                kid_version=version,
                ast=ast,
                valid_from=draft.valid_from,
                valid_to=draft.valid_to,
            )
            for chunk_id in chunk_ids
        ]

    async def publish(
        self,
        draft: CardDraft,
        *,
        vector_only: bool = False,
        expected_version: int | None = None,
    ) -> PublishResult:
        async with self._advisory_lock(draft.language):
            return await self._publish_impl(
                draft, vector_only=vector_only, expected_version=expected_version
            )

    async def _publish_impl(
        self,
        draft: CardDraft,
        *,
        vector_only: bool = False,
        expected_version: int | None = None,
    ) -> PublishResult:
        language = draft.language
        async with self._lock(language):
            await self._assert_not_frozen(language)
            async with self._db.acquire() as connection:
                issues = await validate_draft(connection, draft)
                if issues:
                    raise PublishRejected(issues)
                conflicts = await find_conflicts(connection, draft)
                if conflicts:
                    await enqueue_conflict(
                        connection,
                        kid=draft.kid,
                        language=language,
                        conflicts=[conflict.kid for conflict in conflicts],
                    )
                    raise PublishConflictError(conflicts)
            async with self._db.acquire() as connection:
                workspace_now = await self._registry.resolve(language)
            if (
                workspace_now.embedding_model != self._workspace.embedding_model
                or workspace_now.embedding_dim != self._workspace.embedding_dim
            ):
                raise PublishRejected(
                    [
                        ValidationIssue(
                            "model_drift",
                            "registry embedding 配置与当前 RAG 实例不一致",
                        )
                    ]
                )

            async with self._db.acquire() as connection:
                existing = await get_card(connection, draft.kid)
            current_version = int(existing["current_version"]) if existing else 0
            if expected_version is not None and expected_version != current_version:
                raise PublishFailed(
                    f"迟到写被拒绝：expected_version={expected_version} "
                    f"current_version={current_version}"
                )
            unchanged = await self._unchanged(draft)
            if unchanged is not None:
                return unchanged
            version = current_version + 1 if existing else 1
            if existing:
                await self._registry.bump_generation(language)
                deletion = await self._retry(lambda: self._rag.adelete_by_doc_id(draft.kid))
                if deletion.status not in {"success", "not_found"}:
                    raise PublishFailed(f"改版删除旧投影失败：{deletion.status} {deletion.message}")

            try:
                await self._ingest(draft, vector_only=vector_only)
            except Exception as exc:
                raise PublishFailed(f"摄取失败，卡片暂不可答（fail-closed）：{exc}") from exc

            async with self._db.acquire() as connection:
                chunk_ids = await read_doc_chunk_ids(
                    connection, workspace=language, doc_id=draft.kid
                )
            if not chunk_ids:
                raise PublishFailed("摄取后未读到任何分块，拒绝提交发布")
            rows = self._chunk_rows(draft, chunk_ids, version)

            try:
                async with self._db.transaction() as connection:
                    if existing:
                        await add_card_version(connection, draft, version=version)
                    else:
                        await create_card(connection, draft)
                    await merge_chunk_filter(connection, rows)
                    updated = await backfill_chunk_tables(
                        connection,
                        tables=self._tables,
                        workspace=language,
                        rows=rows,
                    )
                    if updated != {"doc_chunks": len(rows), "vdb_chunks": len(rows)}:
                        raise PublishFailed(f"回填行数不一致：{updated}")
                    if not vector_only:
                        await recompute_candidate_scopes(
                            connection, language=language, workspace=language
                        )
                    differences = await assert_same_scope(
                        connection,
                        tables=self._tables,
                        workspace=language,
                        chunk_ids=chunk_ids,
                    )
                    if differences:
                        raise PublishFailed(f"两表 scope 不一致：{differences[:3]}")
                    pending = await connection.fetchval(
                        f"SELECT count(*) FROM {self._tables['doc_chunks']} "
                        "WHERE workspace = $1 AND id = ANY($2::text[]) "
                        "AND publish_state = 'PENDING'",
                        language,
                        chunk_ids,
                    )
                    if pending:
                        raise PublishFailed(f"仍有 {pending} 行 PENDING")
                    generation = await self._registry.bump_generation(language, connection)
                    await self._registry.refresh_next_boundary(language, connection)
            except Exception as exc:
                raise PublishFailed(f"发布事务失败（投影保持 deny）：{exc}") from exc

            async with self._db.acquire() as connection:
                report = await reconcile_card(connection, draft.kid, freeze_on_failure=True)
            if not report.ok:
                raise PublishReconcileError(report)
            return PublishResult(
                status="published",
                kid=draft.kid,
                version=version,
                chunk_count=len(chunk_ids),
                corpus_generation=int(generation),
                reconcile_ok=True,
            )

    async def withdraw(
        self, kid: str, *, language: str, vector_only: bool = False
    ) -> PublishResult:
        async with self._advisory_lock(language):
            return await self._withdraw_impl(kid, language=language, vector_only=vector_only)

    async def _withdraw_impl(
        self, kid: str, *, language: str, vector_only: bool = False
    ) -> PublishResult:
        async with self._lock(language):
            async with self._db.acquire() as connection:
                card = await get_card(connection, kid)
                if card is None:
                    raise PublishFailed(f"卡片不存在：{kid}")
                if card["language"] != language:
                    raise PublishFailed("撤回语言与卡语言不一致")
                if card["publish_state"] != "PUBLISHED":
                    return PublishResult(
                        status="unchanged",
                        kid=kid,
                        version=int(card["current_version"]),
                        chunk_count=0,
                        corpus_generation=0,
                        reconcile_ok=True,
                    )
            await self._registry.bump_generation(language)
            deletion = await self._retry(lambda: self._rag.adelete_by_doc_id(kid))
            if deletion.status not in {"success", "not_found"}:
                raise PublishFailed(f"撤回删除失败：{deletion.status} {deletion.message}")
            async with self._db.transaction() as connection:
                await set_publish_state(connection, kid, "DEPRECATED")
                if not vector_only:
                    await recompute_candidate_scopes(
                        connection, language=language, workspace=language
                    )
                generation = await self._registry.bump_generation(language, connection)
                await self._registry.refresh_next_boundary(language, connection)
            async with self._db.acquire() as connection:
                report = await reconcile_card(connection, kid, freeze_on_failure=True)
            if not report.ok:
                raise PublishReconcileError(report)
            return PublishResult(
                status="withdrawn",
                kid=kid,
                version=int(card["current_version"]),
                chunk_count=0,
                corpus_generation=int(generation),
                reconcile_ok=True,
            )


async def ensure_authority_schema(database: Database) -> None:
    async with database.transaction() as connection:
        await apply_authority_schema(connection)
