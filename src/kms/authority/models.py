"""权威层写入模型（发布校验与流水线共用）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kms.scope import SET_DIMS


@dataclass(frozen=True, slots=True)
class CardDraft:
    kid: str
    biz_key: str
    title: str
    language: str
    locale: str
    product: tuple[str, ...]
    year: tuple[str, ...]
    security_level: tuple[str, ...]
    valid_from: datetime
    content: dict[str, Any]
    asset: tuple[str, ...] = ()
    region: tuple[str, ...] = ()
    publish_state: str = "PUBLISHED"
    schema_version: str = "v1"
    verification_max: str = "V1"
    action_risk_level: str = "R1"
    family: str | None = None
    valid_to: datetime | None = None
    topic: str = ""
    intent_refs: tuple[str, ...] = ()
    answer_policy: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    review_chain: tuple[dict[str, str], ...] = (
        {"role": "author", "status": "approved"},
        {"role": "reviewer", "status": "approved"},
    )
    risk_level: str = "low"
    timeline_required: bool = False
    timeline_ref: str | None = None
    source_refs: tuple[str, ...] = ("source-1",)
    owner: str = "owner-1"
    change_reason: str = "initial draft"
    refs: tuple[dict[str, Any], ...] = ({"type": "doc", "id": "source-1"},)
    author_id: str = "author-1"
    reviewer_id: str = "reviewer-1"
    published_at: datetime | None = None

    def array(self, dim: str) -> list[str]:
        if dim not in SET_DIMS:
            raise KeyError(dim)
        return list(getattr(self, dim))

    def content_text(self) -> str:
        return json.dumps(self.content, ensure_ascii=False, sort_keys=True)
