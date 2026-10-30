"""§12.2 Nightly 对账：逐语言跑核心五项 + 补充巡检；非零即冻结。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from src.kms.config import load_settings
from src.kms.db import Database
from src.kms.observability.metrics import (
    PUBLISH_QUEUE_DEPTH,
    RECONCILE_LAST_SUCCESS,
    push_metrics,
)
from src.kms.publish.reconcile import reconcile_language
from src.kms.workspace import WorkspaceRegistry

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    settings = load_settings()
    database = Database(settings)
    await database.connect()
    reports = []
    try:
        registry = WorkspaceRegistry(database)
        async with database.acquire() as connection:
            languages = [record.lang for record in await registry.list_all()]
            for language in languages:
                report = await reconcile_language(connection, language, freeze_on_failure=True)
                reports.append(
                    {
                        "language": language,
                        "ok": report.ok,
                        "frozen": report.frozen,
                        "criteria": {
                            name: {
                                "count": criterion.count,
                                "samples": criterion.samples,
                            }
                            for name, criterion in report.criteria.items()
                        },
                    }
                )
            queue_depth = await connection.fetchval(
                "SELECT count(*) FROM conflict_review_queue WHERE status = 'PENDING'"
            )
            PUBLISH_QUEUE_DEPTH.set(int(queue_depth or 0))
    finally:
        await database.close()
    if all(report["ok"] for report in reports):
        RECONCILE_LAST_SUCCESS.set(time.time())
    if settings.pushgateway_url:
        push_metrics(settings.pushgateway_url, job="kms-nightly")
    output_dir = ROOT / "reports" / "m7"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"nightly-{stamp}.json"
    path.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
