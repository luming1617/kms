"""生成黄金集首版（120 条，按语言 × 六维 × 题型分层）。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TOPICS = [
    ("US-BTC-SPOT", "zh-CN", "USA", "SPOT", "2024", "Secret", "BTC"),
    ("UK-BTC-SPOT", "zh-CN", "UK", "SPOT", "2024", "Secret", "BTC"),
    ("CONTRACT-2024", "zh-CN", "USA", "CONTRACT", "2024", "Secret", "BTC"),
    ("US-BTC-2025", "zh-CN", "USA", "SPOT", "2025", "Secret", "BTC"),
    ("JP-TOPSECRET", "zh-CN", "JP", "SPOT", "2024", "TopSecret", "BTC"),
    ("KR-PUBLIC", "zh-CN", "KR", "SPOT", "2024", "Public", "BTC"),
    ("MULTI-REGION-2026", "zh-CN", "USA|UK", "SPOT", "2026", "Secret", "BTC"),
    ("ETH-ASSET", "zh-CN", "USA", "SPOT", "2024", "Secret", "ETH"),
    ("HK-CONFIDENTIAL", "zh-CN", "HK", "SPOT", "2024", "Confidential", "BTC"),
    ("EN-US-SPOT", "en-US", "USA", "SPOT", "2024", "Secret", "BTC"),
    ("JA-JP-SPOT", "ja-JP", "JP", "SPOT", "2024", "Secret", "BTC"),
]
TYPES = [
    ("single_hop", True),
    ("single_hop", True),
    ("single_hop", True),
    ("single_hop", True),
    ("multi_hop", True),
    ("multi_hop", True),
    ("table", True),
    ("numeric", True),
    ("version", True),
    ("conflict", True),
    ("unanswerable", False),
    ("unanswerable", False),
]


def build_case(topic_index: int, variant: int) -> dict:
    name, language, region, product, year, level, asset = TOPICS[topic_index]
    question_type, answerable = TYPES[variant]
    regions = region.split("|")
    scope = {
        "product": [product],
        "year": [year],
        "security_level": level,
        "asset": [asset],
        "region": regions,
    }
    biz_key = f"BIZ-{name}"
    case_id = f"golden-{topic_index:02d}-{variant:02d}"
    if answerable:
        docs = [
            {
                "document_id": name,
                "document_version": 1,
                "biz_key": biz_key,
                "page": "1",
                "section": "规则",
                "evidence": f"{name} 的规则正文片段",
            }
        ]
        reference = f"依据 {biz_key}：{name} 的规则答案。"
        risk = "high" if "TOPSECRET" in name else "low"
    else:
        docs = []
        reference = "暂无可答复内容"
        risk = "low"
    return {
        "case_id": case_id,
        "question_type": question_type,
        "question": f"请说明 {name} 在 {','.join(regions)} 的适用规则与风险提示。",
        "reference_answer": reference,
        "answerable": answerable,
        "language": language,
        "scope": scope,
        "source_documents": docs,
        "risk_level": risk,
        "dataset_version": "golden-v1",
        "review_status": "approved",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="eval/golden")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [
        build_case(topic, variant) for topic in range(len(TOPICS)) for variant in range(len(TYPES))
    ]
    for name, subset in (
        ("locked.jsonl", cases),
        ("dev.jsonl", [c for c in cases if c["answerable"]][:30]),
    ):
        path = output_dir / name
        path.write_text(
            "\n".join(json.dumps(case, ensure_ascii=False) for case in subset) + "\n",
            encoding="utf-8",
        )
        if name == "locked.jsonl":
            (output_dir / "locked.sha256").write_text(
                hashlib.sha256(path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
            )
    print(f"locked={len(cases)}")


if __name__ == "__main__":
    main()
