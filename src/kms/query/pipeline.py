"""八步检索链路：L0 路由 → scope → mix → L2 → 归因/阶梯 → 清洗 → 生成（§九）。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from lightrag import LightRAG, QueryParam

from kms.config import Settings
from kms.db import Database
from kms.observability import tracing
from kms.observability.langfuse import LangfuseObserver, Observation
from kms.observability.metrics import (
    LADDER_RELAXED,
    discard_dimensions,
    observe_llm,
    observe_retrieval,
    record_query_result,
)
from kms.publish.ids import strip_knowledge_prefix
from kms.query.cache import AnswerCache, cache_key
from kms.query.generation import Citation, GenerationResult, generate_answer
from kms.query.l2 import L2Result, assert_channels
from kms.query.l3 import decide
from kms.query.nlu import KeywordProvider, TokenKeywordProvider
from kms.scope import (
    RequestMetrics,
    SessionScope,
    reset_request_metrics,
    reset_scope,
    set_scope,
    start_request_metrics,
)
from kms.workspace import WorkspaceRegistry

LLMCallable = Callable[[str], Awaitable[str]]
logger = logging.getLogger(__name__)


def _set_attr(span_obj: Any, key: str, value: Any) -> None:
    if span_obj is not None:
        span_obj.set_attribute(key, value)


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answered: bool
    answer: str
    refused: bool
    reason: str
    citations: list[Citation] = field(default_factory=list)
    language: str = ""
    scope_signature: str = ""
    corpus_generation: int = 0
    ladder_steps: int = 0
    relaxed_dims: tuple[str, ...] = ()
    shortage: str | None = None
    chunk_count: int = 0
    cache_hit: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    # 仅用于离线模型轨评测；不进入 API 响应，也不进入答案缓存。
    retrieval_context: tuple[str, ...] = ()

    def as_dict(self, *, include_diagnostics: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "answered": self.answered,
            "answer": self.answer,
            "refused": self.refused,
            "reason": self.reason,
            "citations": [{"index": c.index, "biz_key": c.biz_key} for c in self.citations],
            "language": self.language,
            "chunk_count": self.chunk_count,
            "cache_hit": self.cache_hit,
        }
        if include_diagnostics:
            payload.update(
                {
                    "citations": [
                        {"index": c.index, "biz_key": c.biz_key, "chunk_id": c.chunk_id}
                        for c in self.citations
                    ],
                    "scope_signature": self.scope_signature,
                    "corpus_generation": self.corpus_generation,
                    "ladder_steps": self.ladder_steps,
                    "relaxed_dims": list(self.relaxed_dims),
                    "shortage": self.shortage,
                    "metrics": self.metrics,
                }
            )
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, cache_hit: bool = False) -> AnswerResult:
        citations = [Citation(**item) for item in payload.get("citations", [])]
        return cls(
            answered=bool(payload.get("answered")),
            answer=str(payload.get("answer", "")),
            refused=bool(payload.get("refused")),
            reason=str(payload.get("reason", "")),
            citations=citations,
            language=str(payload.get("language", "")),
            scope_signature=str(payload.get("scope_signature", "")),
            corpus_generation=int(payload.get("corpus_generation", 0)),
            ladder_steps=int(payload.get("ladder_steps", 0)),
            relaxed_dims=tuple(payload.get("relaxed_dims", ())),
            shortage=payload.get("shortage"),
            chunk_count=int(payload.get("chunk_count", 0)),
            cache_hit=cache_hit,
            metrics=dict(payload.get("metrics", {})),
        )


class QueryService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        registry: WorkspaceRegistry,
        rag: LightRAG,
        llm: LLMCallable,
        keywords: KeywordProvider | None = None,
        cache: AnswerCache | None = None,
        observer: LangfuseObserver | None = None,
    ) -> None:
        self._settings = settings
        self._db = database
        self._registry = registry
        self._rag = rag
        self._llm = llm
        self._keywords = keywords or TokenKeywordProvider()
        self._cache = cache
        self._observer = observer
        self._last_generation: dict[str, int] = {}

    def _params(
        self,
        *,
        top_k: int,
        chunk_top_k: int,
        max_total_tokens: int,
        hl_keywords: list[str],
        ll_keywords: list[str],
    ) -> QueryParam:
        retrieval = self._settings.retrieval
        return QueryParam(
            mode="mix",
            top_k=top_k,
            chunk_top_k=chunk_top_k,
            max_total_tokens=max_total_tokens,
            max_entity_tokens=retrieval.max_entity_tokens,
            max_relation_tokens=retrieval.max_relation_tokens,
            hl_keywords=hl_keywords,
            ll_keywords=ll_keywords,
            enable_rerank=retrieval.enable_rerank,
        )

    def _observe_generation(
        self, *, duration_seconds: float, status: str, citation_count: int
    ) -> None:
        if self._observer is None:
            return
        # 观测是旁路，不能影响主链路（§11.6）。
        with suppress(Exception):
            self._observer.log_observation(
                Observation(
                    model=self._settings.llm.model_id,
                    operation="answer-generation",
                    prompt_version=self._settings.llm.prompt_version,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=duration_seconds * 1000,
                    status=status,
                    trace_id=tracing.current_trace_id(),
                )
            )

    async def answer(self, session: SessionScope, question: str) -> AnswerResult:
        workspace = await self._registry.resolve(session.language)
        if self._rag.workspace != session.language:
            raise ValueError(
                f"RAG workspace={self._rag.workspace!r} 与请求语言={session.language!r} 不一致"
            )
        ast = session.to_ast()
        generation = workspace.corpus_generation
        cache_key_value: str | None = None
        with tracing.span(
            "rag-answer", {"workspace": session.language, "route": "/v1/answer"}
        ) as root_span:
            if self._settings.enable_answer_cache and self._cache is not None:
                await self._registry.advance_due_boundary(session.language)
                workspace = await self._registry.resolve(session.language)
                generation = workspace.corpus_generation
                previous = self._last_generation.get(session.language)
                if previous is not None and previous != generation:
                    self._cache.clear_generation(session.language, generation)
                self._last_generation[session.language] = generation
                params = {
                    "mode": "mix",
                    "top_k": self._settings.retrieval.top_k,
                    "chunk_top_k": self._settings.retrieval.chunk_top_k,
                    "max_total_tokens": self._settings.retrieval.max_total_tokens,
                    "rerank": self._settings.retrieval.enable_rerank,
                    "rerank_model": self._settings.retrieval.rerank_model,
                    "min_rerank_score": self._settings.retrieval.min_rerank_score,
                }
                cache_key_value = cache_key(
                    question=question,
                    language=session.language,
                    ast=ast,
                    corpus_generation=generation,
                    model_id=self._settings.llm.model_id,
                    prompt_version=self._settings.llm.prompt_version,
                    retrieval_params=params,
                )
                cached = self._cache.get(cache_key_value)
                if cached is not None:
                    cached_result = AnswerResult.from_dict(cached, cache_hit=True)
                    record_query_result(session.language, cached_result)
                    _set_attr(root_span, "cache_hit", True)
                    return cached_result

            hl_keywords, ll_keywords = await self._keywords.keywords(question)
            retrieval = self._settings.retrieval
            top_k = retrieval.top_k
            chunk_top_k = retrieval.chunk_top_k
            max_total_tokens = retrieval.max_total_tokens
            metrics, metrics_token = start_request_metrics()
            metrics.trace_id = tracing.current_trace_id()
            scope_token = set_scope(ast)
            batch_retry_used = False
            params_retry_used = False
            attempt_guard = 0
            framework_failed = False
            l2: L2Result | None = None
            result: AnswerResult
            try:
                async with self._db.acquire() as connection:
                    from kms.authority.repository import resolve_relax_order

                    preference_dims = await resolve_relax_order(connection, retrieval.relax_order)
                while attempt_guard < 8:
                    attempt_guard += 1
                    data: dict[str, Any] = {}
                    retrieval_start = time.perf_counter()
                    with tracing.span(
                        "retrieval", {"mode": "mix", "predicate_present": True}
                    ) as retrieval_span:
                        framework_failed = True
                        attempts = max(1, retrieval.framework_retry_attempts)
                        for attempt in range(attempts):
                            try:
                                response = await self._rag.aquery_data(
                                    question,
                                    self._params(
                                        top_k=top_k,
                                        chunk_top_k=chunk_top_k,
                                        max_total_tokens=max_total_tokens,
                                        hl_keywords=hl_keywords,
                                        ll_keywords=ll_keywords,
                                    ),
                                )
                                if response.get("status") == "success":
                                    data = response.get("data", {}) or {}
                                    framework_failed = False
                                    break
                                if (
                                    response.get("metadata", {}).get("failure_reason")
                                    == "no_results"
                                ):
                                    # 框架的“无结果”是合法空集，不是服务异常。
                                    data = {}
                                    framework_failed = False
                                    break
                            except Exception as exc:
                                logger.debug("aquery_data failed: %s", type(exc).__name__)
                            if attempt + 1 < attempts:
                                await asyncio.sleep(0.05 * (attempt + 1))
                        observe_retrieval(
                            mode="mix",
                            duration_seconds=time.perf_counter() - retrieval_start,
                            result_count=len(data.get("chunks", [])),
                        )
                        _set_attr(retrieval_span, "result_count", len(data.get("chunks", [])))
                    if framework_failed:
                        metrics.shortage = "framework_error"
                        break
                    async with self._db.acquire() as connection:
                        l2 = await assert_channels(
                            connection,
                            data=data,
                            workspace=session.language,
                            ast=ast,
                            metrics=metrics,
                            batch_discard_ratio=retrieval.l2_batch_discard_ratio,
                        )
                        with tracing.span(
                            "context-assembly", {"channel": "chunks", "discarded": 0}
                        ) as assembly_span:
                            decision = await decide(
                                connection,
                                language=session.language,
                                ast=ast,
                                l2=l2,
                                metrics=metrics,
                                min_chunks=retrieval.answer_min_chunks,
                                preference_dims=preference_dims,
                                batch_retry_used=batch_retry_used,
                                params_retry_used=params_retry_used,
                                rerank_enabled=retrieval.enable_rerank,
                            )
                            _set_attr(assembly_span, "discarded", l2.discarded.get("chunks", 0))
                            _set_attr(assembly_span, "ladder_steps", metrics.ladder_steps)
                    if decision.action == "answer":
                        break
                    if decision.action == "abort_batch":
                        metrics.batch_aborted = True
                        metrics.batch_aborted_channels = l2.aborted_channels
                        break
                    if decision.action == "retry_batch":
                        batch_retry_used = True
                        continue
                    if decision.action == "relax" and decision.next_ast is not None:
                        previous_dims = set(ast.relaxed_dims)
                        ast = decision.next_ast
                        set_scope(ast)
                        for dim in ast.relaxed_dims - previous_dims:
                            LADDER_RELAXED.labels(workspace=session.language, dim=dim).inc()
                        metrics.ladder_steps += 1
                        metrics.relaxed_dims = tuple(sorted(ast.relaxed_dims))
                        continue
                    if decision.action == "retry_params":
                        params_retry_used = True
                        top_k = min(top_k * 2, 200)
                        chunk_top_k = min(chunk_top_k * 2, 100)
                        max_total_tokens = min(max_total_tokens * 2, 48000)
                        continue
                    break

                if framework_failed:
                    result = AnswerResult(
                        answered=False,
                        answer="服务暂不可用，请稍后再试",
                        refused=True,
                        reason="service_unavailable",
                        language=session.language,
                        scope_signature=ast.signature(),
                        corpus_generation=generation,
                        ladder_steps=metrics.ladder_steps,
                        relaxed_dims=metrics.relaxed_dims,
                        shortage=metrics.shortage,
                        chunk_count=0,
                        metrics=_metrics_dict(metrics),
                    )
                elif metrics.batch_aborted:
                    result = AnswerResult(
                        answered=False,
                        answer="服务暂不可用，请稍后再试",
                        refused=True,
                        reason="l2_batch_aborted",
                        language=session.language,
                        scope_signature=ast.signature(),
                        corpus_generation=generation,
                        ladder_steps=metrics.ladder_steps,
                        relaxed_dims=metrics.relaxed_dims,
                        shortage=metrics.shortage,
                        chunk_count=0,
                        metrics=_metrics_dict(metrics),
                    )
                elif l2 is None or l2.answerable_count < retrieval.answer_min_chunks:
                    reason = metrics.shortage or "insufficient"
                    result = AnswerResult(
                        answered=False,
                        answer="暂无可答复内容",
                        refused=True,
                        reason=reason,
                        language=session.language,
                        scope_signature=ast.signature(),
                        corpus_generation=generation,
                        ladder_steps=metrics.ladder_steps,
                        relaxed_dims=metrics.relaxed_dims,
                        shortage=metrics.shortage,
                        chunk_count=l2.answerable_count if l2 else 0,
                        metrics=_metrics_dict(metrics),
                    )
                else:
                    llm_start = time.perf_counter()
                    with tracing.span(
                        "answer-generation",
                        {
                            "model": self._settings.llm.model_id,
                            "prompt_version": self._settings.llm.prompt_version,
                            "status": "ok",
                        },
                    ) as gen_span:
                        generated: GenerationResult = await generate_answer(
                            question=question, chunks=l2.chunks, llm=self._llm
                        )
                        _set_attr(
                            gen_span,
                            "status",
                            "answered" if generated.answered else "refused",
                        )
                        _set_attr(gen_span, "citation_count", len(generated.citations))
                    usage = getattr(self._llm, "last_usage", {}) or {}
                    observe_llm(
                        provider="internal",
                        model=self._settings.llm.model_id,
                        operation="answer-generation",
                        status="success" if generated.answered else "refused",
                        duration_seconds=time.perf_counter() - llm_start,
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    )
                    self._observe_generation(
                        duration_seconds=time.perf_counter() - llm_start,
                        status="success" if generated.answered else "refused",
                        citation_count=len(generated.citations),
                    )
                    with tracing.span(
                        "citation-validation", {"citation_count": len(generated.citations)}
                    ):
                        pass
                    result = AnswerResult(
                        answered=generated.answered,
                        answer=generated.answer,
                        refused=not generated.answered,
                        reason=generated.reason,
                        citations=generated.citations,
                        language=session.language,
                        scope_signature=ast.signature(),
                        corpus_generation=generation,
                        ladder_steps=metrics.ladder_steps,
                        relaxed_dims=metrics.relaxed_dims,
                        shortage=metrics.shortage,
                        chunk_count=l2.answerable_count,
                        metrics=_metrics_dict(metrics),
                        retrieval_context=tuple(
                            strip_knowledge_prefix(str(chunk.get("content", "")))
                            for chunk in l2.chunks
                        ),
                    )
            finally:
                reset_scope(scope_token)
                reset_request_metrics(metrics_token)

            record_query_result(session.language, result)
            if (
                self._settings.enable_answer_cache
                and self._cache is not None
                and cache_key_value is not None
                and result.ladder_steps == 0
                and not metrics.batch_aborted
                and not framework_failed
            ):
                self._cache.set(
                    cache_key_value,
                    result.as_dict(include_diagnostics=True),
                    generation=generation,
                    language=session.language,
                )
            _set_attr(root_span, "status", "answered" if result.answered else "refused")
            with tracing.span(
                "final-response",
                {"status": "answered" if result.answered else "refused", "cache_hit": False},
            ):
                pass
            return result


def _metrics_dict(metrics: RequestMetrics | None) -> dict[str, Any]:
    if metrics is None:
        return {}
    return {
        "l1_predicate": metrics.l1_predicate,
        "l1_before": dict(metrics.l1_before),
        "l1_after": dict(metrics.l1_after),
        "l1_requested_chunk_ids": metrics.l1_requested_chunk_ids,
        "l1_missing_chunk_ids": metrics.l1_missing_chunk_ids,
        "l1_pending_dropped": metrics.l1_pending_dropped,
        "l2_entered": metrics.l2_entered,
        "l2_discarded": metrics.l2_discarded,
        "batch_aborted": metrics.batch_aborted,
        "batch_aborted_channels": list(metrics.batch_aborted_channels),
        "shortage": metrics.shortage,
        "relaxed_dims": list(metrics.relaxed_dims),
        "discard_dimensions": discard_dimensions(metrics.discards),
        "discards": [
            {
                "channel": record.channel,
                "chunk_id": record.chunk_id,
                "kid": record.kid,
                "failed_dims": list(record.failed_dims),
                "reason": record.reason,
            }
            for record in metrics.discards
        ],
        "trace_id": metrics.trace_id,
    }
