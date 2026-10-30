"""OpenAI-compatible provider：请求形状、排序与错误。"""

from __future__ import annotations

import httpx
import pytest

from src.kms.providers import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleLLM,
    ProviderError,
)


def embedding_transport(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith("/embeddings")
    payload = __import__("json").loads(request.content)
    assert payload["model"] == "m"
    return httpx.Response(
        200,
        json={"data": [{"index": 1, "embedding": [0.3]}, {"index": 0, "embedding": [0.1]}]},
    )


async def test_embedding_sorted_by_index() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(embedding_transport))
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://llm/v1", api_key="k", client=client
    )
    vectors = await provider.embed(["a", "b"], model="m")
    assert vectors == [[0.1], [0.3]]
    await client.aclose()


async def test_embedding_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleEmbeddingProvider(base_url="http://llm/v1", client=client)
    with pytest.raises(ProviderError):
        await provider.embed(["a"], model="m")
    await client.aclose()


def llm_transport(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith("/chat/completions")
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


async def test_llm_success_and_empty() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(llm_transport))
    llm = OpenAICompatibleLLM(base_url="http://llm/v1", model="m", client=client)
    assert await llm("q") == "ok"
    await client.aclose()

    def empty(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(empty))
    llm = OpenAICompatibleLLM(base_url="http://llm/v1", model="m", client=client)
    with pytest.raises(ProviderError):
        await llm("q")
    await client.aclose()
