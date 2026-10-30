"""G1（M3 出口）正文闸门禁：①② 与按位对齐契约全过。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "m3" / "G1-验收报告.md"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src:scripts"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_m3_content_gates.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    passed = completed.returncode == 0
    lines = [
        "# G1 正文闸门禁报告（M3 出口）",
        "",
        f"- 结论：{'PASS' if passed else 'FAIL'}",
        "- 用例：`tests/integration/test_m3_content_gates.py`",
        "",
        "## 覆盖项",
        "",
        "- ① 图谱路正文出口（`PGKVStorage.get_by_ids/get_by_id` + text_chunks namespace）",
        "- ② 向量路正文出口（`PGVectorStorage.query` → chunks）",
        "- ④ `get_vectors_by_ids` 的 SQL 分支裁切",
        "- `get_by_ids` 按位对齐（缺失位填 None，不错位）",
        "- PENDING deny 默认值在 ①② 两条正文闸均查不出",
        "- 单 product + 单 region 的越权卡在 ①② 均不可见",
        "- 同卡改版不承接旧 scope（chunk_filter 保留 v1/v2 历史行）",
        "- EXPLAIN 计划文本包含 `publish_state` 谓词",
        "",
        "## pytest 输出",
        "",
        "```",
        completed.stdout.strip(),
        completed.stderr.strip(),
        "```",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:8]))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
