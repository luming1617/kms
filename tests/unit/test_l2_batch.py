"""L2 批次丢弃：超阈值必须清空 chunks，不能留幸存者。"""

from __future__ import annotations

import pytest

from src.kms.query import l2 as l2_module
from src.kms.query.l2 import assert_channels
from src.kms.scope import RequestMetrics, SessionScope


def ast():
    return SessionScope(
        language="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level="Secret",
        asset=("BTC",),
        region=("USA",),
    ).to_ast()


async def test_aborted_chunk_batch_is_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_snapshots(*_args, **_kwargs):
        return {}, {}

    monkeypatch.setattr(l2_module, "_load_snapshots", fake_snapshots)
    metrics = RequestMetrics()
    result = await assert_channels(
        None,  # type: ignore[arg-type]
        data={
            "chunks": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
            "entities": [],
            "relationships": [],
        },
        workspace="zh-CN",
        ast=ast(),
        metrics=metrics,
        batch_discard_ratio=0.5,
    )
    assert result.chunks == []
    assert result.answerable_count == 0
    assert "chunks" in result.aborted_channels
    assert result.discarded["chunks"] == 2
