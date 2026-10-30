"""卡级 / 分块标识与内容前缀（§8.3 / §9.1 ⑦）。"""

from __future__ import annotations

import re

KNOWLEDGE_PREFIX = "[KNOWLEDGE:{kid}]"
_PREFIX_PATTERN = re.compile(r"^\s*\[KNOWLEDGE:[^\]]+\]\s*")


def doc_id_for_kid(kid: str) -> str:
    if not kid or kid != kid.strip() or any(ch in kid for ch in "\r\n"):
        raise ValueError(f"非法 kid：{kid!r}")
    return kid


def chunk_id_for(doc_id: str, order: int) -> str:
    return f"{doc_id}-chunk-{order:03d}"


def apply_knowledge_prefix(kid: str, body: str) -> str:
    return f"{KNOWLEDGE_PREFIX.format(kid=kid)}\n{body}"


def strip_knowledge_prefix(text: str) -> str:
    return _PREFIX_PATTERN.sub("", text, count=1)


def contains_knowledge_prefix(text: str) -> bool:
    return "[KNOWLEDGE:" in text
