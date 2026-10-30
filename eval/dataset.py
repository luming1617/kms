"""JSONL 数据集读写与结构校验。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from eval.models import GoldenCase, SourceDocument

ALLOWED_TYPES = {
    "single_hop",
    "multi_hop",
    "table",
    "numeric",
    "version",
    "unanswerable",
    "conflict",
}
ALLOWED_LANGUAGES = {"zh-CN", "en-US", "ja-JP"}
SCOPE_DIMS = ("product", "year", "security_level", "asset", "region")


def load_cases(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            cases.append(
                GoldenCase(
                    case_id=payload["case_id"],
                    question_type=payload["question_type"],
                    question=payload["question"],
                    reference_answer=payload["reference_answer"],
                    answerable=bool(payload["answerable"]),
                    language=payload["language"],
                    scope=payload["scope"],
                    source_documents=tuple(
                        SourceDocument(**doc) for doc in payload.get("source_documents", [])
                    ),
                    risk_level=payload.get("risk_level", "low"),
                    dataset_version=payload["dataset_version"],
                    review_status=payload.get("review_status", "approved"),
                    publish_state=payload.get("publish_state", "PUBLISHED"),
                    valid_from=payload.get("valid_from"),
                    valid_to=payload.get("valid_to"),
                )
            )
    return cases


def validate_cases(cases: list[GoldenCase], *, expected_version: str = "golden-v1") -> list[str]:
    errors: list[str] = []
    ids = set()
    for case in cases:
        if case.case_id in ids:
            errors.append(f"重复 case_id：{case.case_id}")
        ids.add(case.case_id)
        if case.question_type not in ALLOWED_TYPES:
            errors.append(f"{case.case_id}: 未知题型 {case.question_type}")
        if case.language not in ALLOWED_LANGUAGES:
            errors.append(f"{case.case_id}: 非法语言 {case.language}")
        if case.dataset_version != expected_version:
            errors.append(f"{case.case_id}: 数据集版本不一致")
        if case.answerable and not case.source_documents:
            errors.append(f"{case.case_id}: 可回答样本缺少证据")
        if not case.answerable and case.source_documents:
            errors.append(f"{case.case_id}: 不可回答样本不应带证据")
        if case.answerable:
            for doc in case.source_documents:
                if not (doc.page and doc.section and doc.evidence):
                    errors.append(f"{case.case_id}: 证据缺少 page/section/evidence")
        for dim in SCOPE_DIMS:
            if dim not in case.scope:
                errors.append(f"{case.case_id}: 缺少 scope 维 {dim}")
    return errors


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
