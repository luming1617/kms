"""评测样本模型；证据主键不用 chunk_id（§10.4）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceDocument:
    document_id: str
    document_version: int
    biz_key: str
    page: str = ""
    section: str = ""
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    question_type: str
    question: str
    reference_answer: str
    answerable: bool
    language: str
    scope: dict[str, list[str] | str]
    source_documents: tuple[SourceDocument, ...] = ()
    risk_level: str = "low"
    dataset_version: str = "golden-v1"
    review_status: str = "approved"
    publish_state: str = "PUBLISHED"
    valid_from: str | None = None
    valid_to: str | None = None

    @property
    def allowed_biz_keys(self) -> set[str]:
        return {doc.biz_key for doc in self.source_documents}


@dataclass(frozen=True, slots=True)
class LifecycleStep:
    action: str
    kid: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LifecycleCase:
    case_id: str
    initial_documents: tuple[dict[str, Any], ...]
    operations: tuple[LifecycleStep, ...]
    assertions: tuple[dict[str, Any], ...]
