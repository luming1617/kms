"""多 workspace 路由与 embedding 适配。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.kms.query.router import MultiWorkspaceQueryService
from src.kms.runtime import make_embedding_func
from src.kms.scope import SessionScope
from src.kms.workspace import WorkspaceRejected


class FakeService:
    async def answer(self, session: SessionScope, question: str):
        return SimpleNamespace(language=session.language, question=question)


def scope(language: str) -> SessionScope:
    return SessionScope(
        language=language,
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    )


async def test_router_dispatches_by_language() -> None:
    router = MultiWorkspaceQueryService({"zh-CN": FakeService()})  # type: ignore[arg-type]
    result = await router.answer(scope("zh-CN"), "q")
    assert result.language == "zh-CN"
    with pytest.raises(WorkspaceRejected):
        await router.answer(scope("en-US"), "q")


class FakeEmbeddingProvider:
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        assert model == "m"
        return [[1.0, 2.0] for _ in texts]


async def test_make_embedding_func() -> None:
    record = SimpleNamespace(embedding_model="m", embedding_dim=2)
    func = make_embedding_func(record, FakeEmbeddingProvider())  # type: ignore[arg-type]
    vectors = await func(["a", "b"])
    assert len(vectors) == 2
