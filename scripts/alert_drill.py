"""告警演练：静态校验每条规则引用的指标确实存在；配置 Pushgateway 时再查实际规则。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "m7b" / "alert-drill.md"

# 由脚本通过 Pushgateway 推送、不会注册在 API 进程里的指标。
PUSHED_METRICS = frozenset(
    {
        "knowledge_reconcile_last_success_timestamp_seconds",
        "knowledge_publish_queue_depth",
        "knowledge_eval_faithfulness",
    }
)

RULE_CATEGORY = {
    "KnowledgeApiHigh5xx": "可用性",
    "KnowledgeApiP95Slow": "可用性",
    "NightlyReconcileMissing": "组件与任务",
    "ExposureQueueBacklog": "组件与任务",
    "LeakageNonZero": "LLM 质量与业务风险",
    "FaithfulnessDrop": "LLM 质量与业务风险",
    "KnowledgeL2BatchAborted": "LLM 质量与业务风险",
}


def _declared_metrics() -> set[str]:
    """从指标源码静态取名字，避免在演练脚本里重复注册 Prometheus 指标。"""
    source = (ROOT / "src" / "kms" / "observability" / "metrics.py").read_text(encoding="utf-8")
    names = set(re.findall(r'"(knowledge_[a-z0-9_]+)"', source)) | set(PUSHED_METRICS)
    expanded: set[str] = set()
    for name in names:
        expanded.add(name)
        base = name
        for suffix in ("_total",):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                expanded.add(base)
        expanded.update({f"{base}_bucket", f"{base}_count", f"{base}_sum"})
    return expanded


def _rule_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current: str | None = None
    expr_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- alert:"):
            if current:
                blocks.append((current, " ".join(expr_lines)))
            current = line.split(":", 1)[1].strip()
            expr_lines = []
        elif current and line.startswith("expr:"):
            expr_lines.append(line.split(":", 1)[1].strip())
        elif (
            current and expr_lines and not line.startswith(("-", "for:", "labels:", "annotations:"))
        ):
            expr_lines.append(line)
        elif current and line.startswith(("labels:", "annotations:")):
            continue
    if current:
        blocks.append((current, " ".join(expr_lines)))
    return blocks


def run_drills() -> list[dict[str, object]]:
    text = (ROOT / "docker" / "alerts.yml").read_text(encoding="utf-8")
    declared = _declared_metrics()
    results: list[dict[str, object]] = []
    for rule, expr in _rule_blocks(text):
        metrics = sorted(set(re.findall(r"\b(knowledge_[a-z0-9_]+)\b", expr)))
        missing = [name for name in metrics if name not in declared]
        results.append(
            {
                "category": RULE_CATEGORY.get(rule, "未分类"),
                "rule": rule,
                "signal": expr,
                "fired": bool(metrics) and not missing,
                "missing": missing,
            }
        )
    return results


def main() -> int:
    drills = run_drills()
    lines = [
        "# M7b 告警演练报告",
        "",
        "| 类别 | 规则 | 引用指标全部存在 | 缺失指标 |",
        "|---|---|---|---|",
    ]
    for drill in drills:
        lines.append(
            f"| {drill['category']} | {drill['rule']} | "
            f"{'YES' if drill['fired'] else 'NO'} | {', '.join(drill['missing']) or '-'} |"
        )
    lines += [
        "",
        "- 规则文件：`docker/alerts.yml`；指标声明来自 `src/kms/observability/metrics.py`。",
        "- 运维指标由 `nightly_reconcile.py` / `run_model_track.py` 通过 Pushgateway 推送。",
        "- 该演练不代替 Prometheus 端到端告警测试；CI 至少阻断“规则引用不存在的指标”。",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:8]))
    return 0 if drills and all(drill["fired"] for drill in drills) else 1


if __name__ == "__main__":
    raise SystemExit(main())
