"""M0 探测脚本公共设施：环境、假模型、LightRAG 构建、报告落盘。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

REPORT_DIR = Path(os.environ.get("ICS_REPORT_DIR", str(PROJECT_ROOT / "reports" / "m0" / "final")))

import asyncpg  # noqa: E402
import numpy as np  # noqa: E402
from lightrag import LightRAG  # noqa: E402
from lightrag.utils import EmbeddingFunc  # noqa: E402

from src.kms.config import Settings  # noqa: E402

FAKE_GRAPH_ANSWER = (
    "entity<|#|>BTC<|#|>ASSET<|#|>BTC 是一种加密资产，支持现货交易。\n"
    "entity<|#|>USA<|#|>REGION<|#|>美国市场，支持 BTC 现货。\n"
    "relation<|#|>BTC<|#|>USA<|#|>spot, region<|#|>BTC 在美国市场支持现货交易。\n"
    "<|COMPLETE|>"
)


def configure_environment(settings: Settings | None = None) -> Settings:
    settings = settings or Settings.from_env()
    os.environ.pop("POSTGRES_WORKSPACE", None)
    os.environ.update(settings.postgres.lightrag_env())
    settings.validate_startup()
    return settings


def deterministic_vector(text: str, dim: int) -> np.ndarray:
    vector = np.zeros(dim, dtype=np.float32)
    for token in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def make_embedding_func(dim: int = 32, model_name: str = "probe-embed") -> EmbeddingFunc:
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
        return FAKE_GRAPH_ANSWER
    return "ok"


def build_rag(
    settings: Settings,
    *,
    workspace: str = "zh-CN",
    dim: int = 32,
    model_name: str = "probe-embed",
    cosine_threshold: float = 0.05,
) -> LightRAG:
    return LightRAG(
        working_dir="/tmp/kms_probe",
        llm_model_func=fake_llm,
        embedding_func=make_embedding_func(dim=dim, model_name=model_name),
        kv_storage="PGKVStorage",
        vector_storage="PGVectorStorage",
        graph_storage="PGTableGraphStorage",
        doc_status_storage="PGDocStatusStorage",
        workspace=workspace,
        vector_db_storage_cls_kwargs={"cosine_better_than_threshold": cosine_threshold},
        enable_llm_cache=False,
        enable_llm_cache_for_entity_extract=True,
    )


async def reset_schema(settings: Settings, database: str | None = None) -> None:
    conn = await asyncpg.connect(settings.postgres.dsn(database))
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await conn.close()


def write_report(name: str, payload: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    if path.exists():
        return path
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path


def print_summary(payload: dict[str, Any]) -> None:
    passed = payload.get("passed")
    print(json.dumps({"probe": payload.get("probe"), "passed": passed}, ensure_ascii=False))


def run(coro: Any) -> None:
    asyncio.run(coro)
