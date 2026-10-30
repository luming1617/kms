"""补齐 L3 预算分支、缓存淘汰、Trace 采样与 SQL 构造的单测。"""

from __future__ import annotations

import pytest

from src.kms.authority.repository import validity_intersects
from src.kms.observability import tracing
from src.kms.query.cache import InMemoryAnswerCache, cache_key, normalize_question
from src.kms.query.generation import generate_answer
from src.kms.query.l2 import L2Result
from src.kms.query.l3 import decide
from src.kms.scope import FilterAST, RequestMetrics, SessionScope
from src.kms.storage.kv import (
    _normalize_text_chunk_row,
    build_text_chunk_by_id_sql,
)
from src.kms.storage.vector import build_vectors_by_ids_sql


def ast() -> FilterAST:
    return SessionScope(
        language="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    ).to_ast()


class FakeConnection:
    def __init__(self, values: list[int]) -> None:
        self._values = iter(values)

    async def fetchval(self, *_args: object, **_kwargs: object) -> int:
        return next(self._values)


async def test_l3_budget_then_exhausted() -> None:
    metrics = RequestMetrics(l1_before={"chunks": 10}, l1_after={"chunks": 0})
    l2 = L2Result(entered={"chunks": 0}, discarded={"chunks": 0})
    connection = FakeConnection([10, 10])
    decision = await decide(
        connection,
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=False,
    )
    assert decision.action == "retry_params"
    connection = FakeConnection([10, 10])
    decision = await decide(
        connection,
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=True,
    )
    assert decision.action == "exhausted"


async def test_l3_preference_and_auth_decisions() -> None:
    metrics = RequestMetrics(l1_before={"chunks": 10}, l1_after={"chunks": 0})
    l2 = L2Result()
    decision = await decide(
        FakeConnection([10, 0]),
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=False,
    )
    assert decision.action == "relax" and decision.next_ast is not None
    decision = await decide(
        FakeConnection([0, 0]),
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=False,
    )
    assert decision.action == "empty_auth"
    l2.aborted_channels = ("chunks",)
    decision = await decide(
        FakeConnection([10, 10]),
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=(),
        batch_retry_used=False,
        params_retry_used=False,
    )
    assert decision.action == "retry_batch"
    l2.chunks = [{"chunk_id": "c1"}]
    decision = await decide(
        FakeConnection([10, 10]),
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=1,
        preference_dims=(),
        batch_retry_used=True,
        params_retry_used=False,
    )
    assert decision.action == "abort_batch"


async def test_l3_score_threshold_is_not_budget_retry() -> None:
    metrics = RequestMetrics(l1_before={"chunks": 10}, l1_after={"chunks": 0})
    l2 = L2Result(entered={"chunks": 0})
    decision = await decide(
        FakeConnection([10, 10]),
        language="zh-CN",
        ast=ast(),
        l2=l2,
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=False,
        rerank_enabled=True,
    )
    assert decision.action == "exhausted" and decision.reason == "score_threshold"


async def test_l3_missing_attribution_never_relaxes() -> None:
    metrics = RequestMetrics()
    decision = await decide(
        FakeConnection([10, 10]),
        language="zh-CN",
        ast=ast(),
        l2=L2Result(),
        metrics=metrics,
        min_chunks=2,
        preference_dims=("asset", "region"),
        batch_retry_used=False,
        params_retry_used=False,
    )
    assert decision.action == "exhausted" and decision.reason == "attribution_missing"


def test_error_sampler_accepts_positional_args() -> None:
    sampler = tracing.ErrorPrioritySampler(1.0)
    result = sampler.should_sample(None, 12345, "retrieval", None, {}, None)
    assert result.decision.is_sampled()


def test_cache_ttl_clear_and_key_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = InMemoryAnswerCache()
    monkeypatch.setattr("kms.query.cache.time.monotonic", lambda: 100.0)
    cache.set("zh-CN:abc", {"answer": "x"}, ttl_seconds=1)
    assert cache.get("zh-CN:abc") is not None
    monkeypatch.setattr("kms.query.cache.time.monotonic", lambda: 200.0)
    assert cache.get("zh-CN:abc") is None
    cache.set("prefix:abc", {"answer": "x"})
    cache.clear_generation("zh-CN", 1)
    prefix = cache_key(
        question="q",
        language="zh-CN",
        ast=ast(),
        corpus_generation=1,
        model_id="m",
        prompt_version="v1",
        retrieval_params={},
    ).split(":")[0]
    cache.set(prefix + ":old", {"answer": "old"}, generation=1, language="zh-CN")
    cache.set(prefix + ":new", {"answer": "new"}, generation=2, language="zh-CN")
    cache.clear_generation("zh-CN", 2)
    assert cache.get(prefix + ":old") is None
    assert cache.get(prefix + ":new") is not None
    assert normalize_question("  A   B ", "en-US") == "a b"
    key = cache_key(
        question=" q ",
        language="zh-CN",
        ast=ast(),
        corpus_generation=1,
        model_id="m",
        prompt_version="v1",
        retrieval_params={"top_k": 1},
    )
    assert key.startswith(
        cache_key(
            question="q",
            language="zh-CN",
            ast=ast(),
            corpus_generation=1,
            model_id="m",
            prompt_version="v1",
            retrieval_params={"top_k": 1},
        ).split(":")[0]
    )


def test_validity_intersects() -> None:
    from datetime import UTC, datetime

    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    assert validity_intersects(start, end, datetime(2024, 6, 1, tzinfo=UTC), None)
    assert not validity_intersects(start, end, end, None)


def test_kv_by_id_sql_and_row_normalization() -> None:
    sql, params = build_text_chunk_by_id_sql(ast())
    assert "publish_state = $8" in sql and len(params) == 7
    row = _normalize_text_chunk_row({"llm_cache_list": "[]", "heading": None, "sidecar": "bad"})
    assert row is not None and row["llm_cache_list"] == [] and row["heading"] == {}
    assert _normalize_text_chunk_row(None) is None


def test_vector_ids_sql_for_candidates() -> None:
    sql, params = build_vectors_by_ids_sql("LIGHTRAG_VDB_ENTITY_test", ast(), for_chunks=False)
    assert "publish_state" not in sql and len(params) == 5


async def test_generation_refusals() -> None:
    assert (await generate_answer(question="q", chunks=[], llm=lambda _: None)).reason.startswith(
        "no_source"
    )

    async def boom(_prompt: str) -> str:
        raise RuntimeError("down")

    failed = await generate_answer(
        question="q", chunks=[{"chunk_id": "c", "content": "x"}], llm=boom
    )
    assert failed.reason.startswith("llm_error")

    async def out_of_range(_prompt: str) -> str:
        return "答案 [9]"

    result = await generate_answer(
        question="q",
        chunks=[{"chunk_id": "c", "content": "x", "biz_key": "BIZ"}],
        llm=out_of_range,
    )
    assert result.reason == "citation_out_of_range"


def test_trace_sampler_and_mark_error() -> None:
    sampler = tracing.ErrorPrioritySampler(ratio=1.0)
    sampled = sampler.should_sample(trace_id=123, attributes={"error": True})
    assert sampled.decision.name == "RECORD_AND_SAMPLE"
    dropped = sampler.should_sample(trace_id=1, attributes={})
    assert dropped.decision.name in {"DROP", "RECORD_AND_SAMPLE"}
    tracing.mark_error(None, RuntimeError("x"))
    assert tracing.sanitize_attributes({"count": 1, "answer": "x"}) == {"count": 1}
