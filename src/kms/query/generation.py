"""自有 LLM 生成：只装通过 L2 的正文；清洗前缀；校验引用（§9.1 ⑦⑧）。"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kms.publish.ids import contains_knowledge_prefix, strip_knowledge_prefix

_CITATION = re.compile(r"\[(\d+)\]")


class GenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Citation:
    index: int
    biz_key: str
    chunk_id: str


@dataclass
class GenerationResult:
    answered: bool
    answer: str
    citations: list[Citation] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def build_sources(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped_prefix = 0
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id", ""))
        if not chunk_id or chunk_id in seen:
            continue
        content = strip_knowledge_prefix(str(chunk.get("content", "")))
        if contains_knowledge_prefix(content):
            dropped_prefix += 1
            continue
        seen.add(chunk_id)
        sources.append(
            {
                "index": len(sources) + 1,
                "chunk_id": chunk_id,
                "kid": chunk.get("kid"),
                "biz_key": chunk.get("biz_key"),
                "content": content,
            }
        )
    return sources, dropped_prefix


def build_prompt(question: str, sources: list[dict[str, Any]]) -> str:
    blocks = []
    for source in sources:
        blocks.append(f"[{source['index']}] (biz:{source['biz_key']})\n{source['content']}")
    return (
        "你是智能客服知识助手。只依据给定资料回答；每个事实句必须标注来源编号，如 [1]。\n"
        "资料不足时明确回答“暂无可答复内容”。不得输出任何内部标记。\n\n"
        f"问题：{question}\n\n资料：\n" + "\n\n".join(blocks)
    )


async def generate_answer(
    *,
    question: str,
    chunks: list[dict[str, Any]],
    llm: Callable[[str], Awaitable[str]],
) -> GenerationResult:
    sources, dropped = build_sources(chunks)
    if not sources:
        return GenerationResult(
            answered=False, answer="暂无可答复内容", reason=f"no_source(prefix_dropped={dropped})"
        )
    prompt = build_prompt(question, sources)
    try:
        raw = await llm(prompt)
    except Exception as exc:
        return GenerationResult(
            answered=False, answer="服务暂不可用，请稍后再试", reason=f"llm_error:{exc}"
        )
    answer = strip_knowledge_prefix(str(raw)).strip()
    if contains_knowledge_prefix(answer):
        return GenerationResult(answered=False, answer="暂无可答复内容", reason="prefix_residual")
    indexes = [int(match) for match in _CITATION.findall(answer)]
    if not indexes:
        return GenerationResult(answered=False, answer="暂无可答复内容", reason="no_citation")
    by_index = {source["index"]: source for source in sources}
    if any(index not in by_index for index in indexes):
        return GenerationResult(
            answered=False, answer="暂无可答复内容", reason="citation_out_of_range"
        )
    citations = [
        Citation(
            index=index,
            biz_key=str(by_index[index]["biz_key"]),
            chunk_id=str(by_index[index]["chunk_id"]),
        )
        for index in sorted(set(indexes))
    ]
    return GenerationResult(answered=True, answer=answer, citations=citations, sources=sources)
