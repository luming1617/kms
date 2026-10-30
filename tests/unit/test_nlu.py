"""关键词前置：LLM JSON 解析与失败回退。"""

from __future__ import annotations

from src.kms.query.nlu import LlmKeywordProvider, TokenKeywordProvider


class FakeLlm:
    def __init__(self, text: str) -> None:
        self.text = text

    async def __call__(self, _prompt: str) -> str:
        return self.text


async def test_llm_keyword_provider_parses_json() -> None:
    provider = LlmKeywordProvider(FakeLlm('{"high_level": ["交易"], "low_level": ["BTC", "USA"]}'))
    high, low = await provider.keywords("BTC 在 USA 的交易规则")
    assert high == ["交易"] and low == ["BTC", "USA"]


async def test_llm_keyword_provider_falls_back() -> None:
    provider = LlmKeywordProvider(FakeLlm("not json"))
    high, low = await provider.keywords("BTC USA")
    assert high and low
    fallback = await TokenKeywordProvider().keywords("BTC USA")
    assert (high, low) == fallback
