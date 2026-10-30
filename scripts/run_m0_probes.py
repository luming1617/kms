"""M0 总入口：顺序运行五项探测并生成验收报告。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "m0" / "final"
PROBES = [
    ("01-tables", "probe_tables.py"),
    ("02-signatures", "probe_signatures.py"),
    ("03-chunk-ids", "probe_chunk_ids.py"),
    ("04-index-cost", "probe_index_cost.py"),
    ("05-load", "probe_load.py"),
]


def env() -> dict[str, str]:
    current = os.environ.copy()
    path = f"{ROOT / 'src'}:{ROOT / 'scripts'}"
    current["PYTHONPATH"] = path + (
        ":" + current["PYTHONPATH"] if current.get("PYTHONPATH") else ""
    )
    current.setdefault("POSTGRES_HOST", "127.0.0.1")
    current.setdefault("POSTGRES_PORT", "5434")
    current.setdefault("POSTGRES_USER", "ics")
    current.setdefault("POSTGRES_PASSWORD", "ics")
    current.setdefault("POSTGRES_DATABASE", "ics")
    current.pop("POSTGRES_WORKSPACE", None)
    return current


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, bool]] = []
    for name, script in PROBES:
        log_dir = Path("/tmp/ics_m0_logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}.log"
        print(f"[M0] running {name} ...", flush=True)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "probes" / script)],
            cwd=ROOT,
            env=env(),
            capture_output=True,
            text=True,
        )
        log_path.write_text(
            completed.stdout + "\n--- stderr ---\n" + completed.stderr, encoding="utf-8"
        )
        report_path = REPORT_DIR / f"{name}.json"
        passed = completed.returncode == 0 and report_path.exists()
        if passed:
            passed = bool(json.loads(report_path.read_text(encoding="utf-8")).get("passed"))
        results.append((name, passed))
        print(f"[M0] {name}: {'PASS' if passed else 'FAIL'}", flush=True)

    lines = [
        "# M0 探测与基线验收报告",
        "",
        "| 探测 | 结论 | 报告 |",
        "|---|---|---|",
    ]
    for name, passed in results:
        lines.append(f"| {name} | {'PASS' if passed else 'FAIL'} | `{name}.json` |")

    load = json.loads((REPORT_DIR / "05-load.json").read_text(encoding="utf-8"))
    cost = json.loads((REPORT_DIR / "04-index-cost.json").read_text(encoding="utf-8"))
    lines += [
        "",
        "## 实测数字",
        "",
        "| 项 | 实测 |",
        "|---|---|",
        f"| 检索 QPS（mix / rerank 关闭 / 并发 10） | {load['qps']} |",
        f"| P95 延迟 | {load['latency_ms']['p95']} ms |",
        f"| P50 延迟 | {load['latency_ms']['p50']} ms |",
        f"| 压测分块数 | {load['chunk_count']} |",
        f"| 10 万行 ADD COLUMN | {cost['timings_seconds']['add_columns_ddl']} s"
        f"（不重写堆：{not cost['ddl_rewrote_heap']}） |",
        f"| 10 万行回填 scope | {cost['timings_seconds']['backfill_100k_rows']} s |",
        f"| GIN 建索引 | {cost['timings_seconds']['create_gin']} s / "
        f"{round(cost['gin_index_bytes'] / 1024 / 1024, 2)} MB |",
        "",
        "> QPS=1000 / P95<=5s 是服务目标，不是本机单实例基线；M0 只做机制级标定，容量结论留给 M7。",
        "",
        "## 关键结论",
        "",
        "- 真实表名由 `namespace_to_table_name()` + `_generate_collection_suffix()` 解析，"
        "最长基表 `LIGHTRAG_VDB_RELATION` 21 字符；"
        "模型名规范化后后缀必须 <= 41 字符。",
        "- 9 个方法的签名与附录 B 一致；10 个注入点中 "
        "`get_by_ids` / `get_by_id` 必须保持按位对齐。",
        "- 默认路径 chunk_id 实测为 `{doc_id}-chunk-{order:03d}`，位置型、与内容无关。",
        "- `LIGHTRAG_DOC_CHUNKS` 的 10 列增列在 PG18 上为元数据级操作（恒定默认值），不重写堆。",
        "- 检索基线在 rerank 关闭下测得；外部 rerank 的出境与延迟待 M6 白名单结论后再标定。",
        "",
        "## 与方案不一致项（待回写）",
        "",
        "1. 本机安装的 LightRAG 1.5.7 构建把 `process_options` 放在 `apipeline_enqueue_documents`，"
        "`apipeline_process_enqueue_documents()` 不接受该参数。§8.3 的调用顺序需按实际签名执行。",
        "2. `PGTableGraphStorage` 的删边方法是 `remove_edges`（不是 `delete_edge`），"
        "写路径护栏需按实际方法名覆盖。",
        "3. 方案附录 B 行号基于另一构建；本机版本的关键方法行号以 "
        "`02-signatures.json` 的 `line` 字段为准。",
        "",
        "> 机制（M0）结论不改变方案口径；真实规模与 rerank 曲线分别留到 M7 / M8。",
        "",
    ]
    report = REPORT_DIR / "M0-验收报告.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report.read_text(encoding="utf-8"))
    return 0 if all(passed for _, passed in results) else 1


if __name__ == "__main__":
    sys.exit(main())
