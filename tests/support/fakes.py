"""测试用确定性 embedding / LLM，以及 PG schema 与 registry 准备。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import asyncpg
import numpy as np
from lightrag.utils import EmbeddingFunc

from src.kms.config import Settings
from src.kms.workspace import ensure_registry_schema, seed_language

GRAPH_ANSWER = (
    "entity<|#|>BTC<|#|>ASSET<|#|>BTC 是一种加密资产，支持现货交易。\n"
    "entity<|#|>USA<|#|>REGION<|#|>美国市场，支持 BTC 现货。\n"
    "relation<|#|>BTC<|#|>USA<|#|>spot, region<|#|>BTC 在美国市场支持现货交易。\n"
    "<|COMPLETE|>"
)


def deterministic_vector(text: str, dim: int) -> np.ndarray:
    vector = np.zeros(dim, dtype=np.float32)
    for token in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def make_embedding_func(dim: int = 32, model_name: str = "test-embed") -> EmbeddingFunc:
    async def _embed(texts: list[str], **_kwargs: Any) -> np.ndarray:
        return np.vstack([deterministic_vector(text, dim) for text in texts])

    return EmbeddingFunc(embedding_dim=dim, func=_embed, model_name=model_name)


async def fake_llm(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict[str, str]] | None = None,
    **_kwargs: Any,
) -> str:
    del system_prompt, history_messages
    lowered = prompt.lower()
    if "entity" in lowered and "relationship" in lowered:
        return GRAPH_ANSWER
    return "ok"


async def reset_database(settings: Settings) -> None:
    connection = await asyncpg.connect(settings.postgres.dsn())
    try:
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await connection.execute("CREATE SCHEMA public")
        await connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await connection.close()


async def seed_default_languages(
    settings: Settings, *, model: str = "test-embed", dim: int = 32
) -> None:
    connection = await asyncpg.connect(settings.postgres.dsn())
    try:
        await ensure_registry_schema(connection)
        for lang in ("zh-CN", "en-US", "ja-JP"):
            await seed_language(connection, lang=lang, embedding_model=model, embedding_dim=dim)
    finally:
        await connection.close()
