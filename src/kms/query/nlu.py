"""关键词前置：策略 A 关闭框架 keywords 缓存，由前置 NLU 预传（§7.3 / §9.3）。"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol


class KeywordProvider(Protocol):
    async def keywords(self, question: str) -> tuple[list[str], list[str]]: ...


def _fallback_tokens(question: str) -> tuple[list[str], list[str]]:
    tokens = [token for token in re.split(r"[\s,，。；;:：!?！？]+", question) if token]
    if not tokens:
        return ["知识"], ["知识"]
    return tokens[:2], tokens[:4]


class TokenKeywordProvider:
    """离线兜底：按词切分；线上由 LlmKeywordProvider 替代。"""

    async def keywords(self, question: str) -> tuple[list[str], list[str]]:
        return _fallback_tokens(question)


class LlmKeywordProvider:
    """用 LLM 抽取 high/low level 关键词；失败时退回词切分，不阻断检索。"""

    def __init__(self, llm: Any, *, fallback: KeywordProvider | None = None) -> None:
        self._llm = llm
        self._fallback = fallback or TokenKeywordProvider()

    async def keywords(self, question: str) -> tuple[list[str], list[str]]:
        prompt = (
            "从问题中抽取检索关键词。只输出 JSON："
            '{"high_level": ["..."], "low_level": ["..."]}。'
            "high_level 是抽象主题/关系，low_level 是具体实体/术语。\n"
            f"问题：{question}"
        )
        try:
            raw = await self._llm(prompt)
            match = re.search(r"\{.*\}", str(raw), re.DOTALL)
            if not match:
                raise ValueError("no json")
            payload = json.loads(match.group(0))
            high = [str(item) for item in payload.get("high_level", []) if str(item).strip()]
            low = [str(item) for item in payload.get("low_level", []) if str(item).strip()]
            if not high or not low:
                raise ValueError("empty keywords")
            return high[:8], low[:8]
        except Exception:
            return await self._fallback.keywords(question)
