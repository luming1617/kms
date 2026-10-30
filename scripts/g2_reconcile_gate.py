"""G2（M5 出口）对账门禁：§12.2 核心五项 + 补充巡检全 0，且发布幂等。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "m5" / "G2-验收报告.md"


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src:scripts"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/integration/test_m5_publish_pipeline.py",
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
        "# G2 对账门禁报告（M5 出口）",
        "",
        f"- 结论：{'PASS' if passed else 'FAIL'}",
        "- 用例：`tests/integration/test_m5_publish_pipeline.py`",
        "",
        "## 覆盖项",
        "",
        "- §12.2 核心五项 + 补充巡检全 0（发布后自动对账）；",
        "- 同一发布重放两次 → `unchanged`，版本与 chunk_filter 行数不变；",
        "- 试发布 → 改版 → 撤回 → 重发闭环；撤回后投影不可检索、chunk_filter 历史保留；",
        "- 冲突卡进入 `conflict_review_queue`，不产生版本；",
        "- 对账不平 → 冻结语言发布队列，新发布被 `PublishFrozenError` 拦截；",
        "- 摄取失败 → 不写权威快照，投影保持 deny（fail-closed）。",
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
    print("\n".join(lines[:7]))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
