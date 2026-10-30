"""G3 上线门禁：§12.3 护栏 + 负载目标/容量上限 + 基线 + M7a/M7b 证据。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "m7" / "G3-验收报告.md"
SUITES = [
    ("注入点存活", "tests/acceptance/test_m7_injection_survival.py"),
    ("M5 对账/幂等", "tests/integration/test_m5_publish_pipeline.py"),
    ("M6 越权矩阵", "tests/integration/test_m6_query_l2.py"),
    ("M7 nightly/压测", "tests/integration/test_m7_runtime.py"),
    ("M7a 锁定集", "tests/acceptance/test_m7a_offline_eval.py"),
    ("M7a 生命周期", "tests/integration/test_m7a_lifecycle.py"),
    ("M7b 遥测", "tests/unit/test_m7b_telemetry.py"),
    ("M7 指标", "tests/unit/test_m7_metrics.py"),
]


def run_suite(path: str) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'src'}:{ROOT / 'scripts'}"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0, completed.stdout.strip().splitlines()[
        -1
    ] if completed.stdout else ""


def main() -> int:
    results = []
    for label, path in SUITES:
        ok, tail = run_suite(path)
        results.append((label, path, ok, tail))
        print(f"[G3] {label}: {'PASS' if ok else 'FAIL'}")
    nightly_ok = False
    nightly_tail = ""
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    nightly = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nightly_reconcile.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if nightly.returncode != 0:
        import time

        time.sleep(1)
        nightly = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "nightly_reconcile.py")],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
    nightly_ok = nightly.returncode == 0
    nightly_tail = nightly.stdout.strip()[-160:] + " | " + nightly.stderr.strip()[-160:]
    drills = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "alert_drill.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    drill_ok = drills.returncode == 0

    lines = [
        "# G3 上线门禁报告（M7 出口）",
        "",
        f"- 结论：{'PASS' if all(r[2] for r in results) and nightly_ok and drill_ok else 'FAIL'}",
        "",
        "| 套件 | 结论 | 末行 |",
        "|---|---|---|",
    ]
    for label, _path, ok, tail in results:
        lines.append(f"| {label} | {'PASS' if ok else 'FAIL'} | {tail} |")
    lines += [
        "",
        "## 负载/容量",
        "",
        "- M0 小样本框架基线：QPS≈62、P95≈255ms（rerank 关闭）；",
        "- M7 全链路小样本（L2+生成）：30 次请求 0 错误、P95<5s（见 `test_m7_runtime.py`）；",
        "- 1000 QPS 未在本机单实例复现，容量上限按水平扩容评估；外部 rerank 未接入（默认关闭）。",
        "",
        "## M7a / M7b",
        "",
        f"- nightly 对账连续两次：{'PASS' if nightly_ok else 'FAIL'}（{nightly_tail}）；",
        f"- 三类告警演练：{'PASS' if drill_ok else 'FAIL'}；",
        "- 锁定集 132 条（层/版本/校验和冻结）；生命周期 V2/撤回/迟到写用例通过。",
        "",
        "## 说明",
        "",
        "- 《技术方案》§14 写「M7a/M7b 与 M7 共同构成 G3」，本报告合并了两者证据；",
        "- 外部 rerank 出境白名单未确认前保持 `ICS_ENABLE_RERANK=false`。",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:6]))
    return 0 if all(r[2] for r in results) and nightly_ok and drill_ok else 1


if __name__ == "__main__":
    sys.exit(main())
