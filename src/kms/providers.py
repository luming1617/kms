"""OpenAI-compatible embedding / chat providers used by the runtime CLI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client
        self.last_usage: dict[str, Any] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def embed(self, texts: Sequence[str], *, model: str) -> list[list[float]]:
        payload = {"model": model, "input": list(texts)}
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self._base_url}/embeddings", json=payload, headers=self._headers()
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/embeddings", json=payload, headers=self._headers()
                    )
            response.raise_for_status()
            body = response.json()
            items = sorted(body["data"], key=lambda item: item.get("index", 0))
            vectors = [item["embedding"] for item in items]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"embedding provider failed: {exc}") from exc
        if len(vectors) != len(texts):
            raise ProviderError("embedding provider returned a different number of vectors")
        return vectors


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def __call__(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
                    )
            response.raise_for_status()
            body = response.json()
            self.last_usage = body.get("usage", {}) or {}
            content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"llm provider failed: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("llm provider returned an empty response")
        return content
