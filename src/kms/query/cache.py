"""可选自研答案缓存：key 含 scope_sig ＋ 语言 ＋ 代次 ＋ 模型/提示版本 ＋ 检索参数。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

from kms.observability.metrics import CACHE_ENTRIES
from kms.scope import FilterAST


class AnswerCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int = 900,
        generation: int | None = None,
        language: str | None = None,
    ) -> None: ...
    def clear_generation(self, language: str, generation: int) -> None: ...
    @property
    def size(self) -> int: ...


@dataclass
class _Entry:
    value: dict[str, Any]
    expires_at: float
    generation: int | None = None
    language: str | None = None


class InMemoryAnswerCache:
    def __init__(self, *, max_entries: int = 2048) -> None:
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._max_entries = max_entries

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._data.pop(key, None)
            CACHE_ENTRIES.set(len(self._data))
            return None
        self._data.move_to_end(key)
        return entry.value

    def set(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int = 900,
        generation: int | None = None,
        language: str | None = None,
    ) -> None:
        self._data[key] = _Entry(
            value=value,
            expires_at=time.monotonic() + ttl_seconds,
            generation=generation,
            language=language,
        )
        self._data.move_to_end(key)
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)
        CACHE_ENTRIES.set(len(self._data))

    def clear_generation(self, language: str, generation: int) -> None:
        """删除该语言下非当前代次的条目；新代次开始后旧条目整批淘汰（§7.3）。"""
        prefix = hashlib.sha256(language.encode("utf-8")).hexdigest()[:8]
        for key, entry in list(self._data.items()):
            if key.startswith(prefix) and entry.generation != generation:
                self._data.pop(key, None)
        CACHE_ENTRIES.set(len(self._data))

    @property
    def size(self) -> int:
        return len(self._data)


def normalize_question(question: str, language: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip())
    if language.startswith("en"):
        normalized = normalized.lower()
    return normalized


def cache_key(
    *,
    question: str,
    language: str,
    ast: FilterAST,
    corpus_generation: int,
    model_id: str,
    prompt_version: str,
    retrieval_params: dict[str, Any],
) -> str:
    payload = {
        "q": normalize_question(question, language),
        "lang": language,
        "scope_sig": ast.signature(),
        "generation": corpus_generation,
        "model": model_id,
        "prompt": prompt_version,
        "params": retrieval_params,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    prefix = hashlib.sha256(language.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}:{digest}"
