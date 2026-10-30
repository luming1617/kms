"""只读管理面 store：只查权威层。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from support.fakes import reset_database

from src.kms.api.admin import AuthorityAdminStore
from src.kms.authority.models import CardDraft
from src.kms.authority.repository import create_card
from src.kms.authority.schema import apply_authority_schema
from src.kms.config import Settings
from src.kms.db import Database

pytestmark = [pytest.mark.m2, pytest.mark.db]


async def test_admin_store_lists_and_reads_authority(settings: Settings) -> None:
    await reset_database(settings)
    database = Database(settings)
    await database.connect()
    try:
        async with database.transaction() as connection:
            await apply_authority_schema(connection)
            await create_card(
                connection,
                CardDraft(
                    kid="ADMIN-K1",
                    biz_key="BIZ-ADMIN-K1",
                    title="管理面卡",
                    language="zh-CN",
                    locale="zh-CN",
                    product=("SPOT",),
                    year=("2024",),
                    security_level=("Secret",),
                    asset=("BTC",),
                    region=("USA",),
                    valid_from=datetime(2024, 1, 1, tzinfo=UTC),
                    content={
                        "title": "管理面卡",
                        "sections": [{"heading": "正文", "body": "管理面正文"}],
                    },
                    topic="admin",
                    change_reason="admin-test",
                ),
            )
        store = AuthorityAdminStore(database)
        cards = await store.list_cards(language="zh-CN", limit=10, offset=0)
        assert [card["kid"] for card in cards] == ["ADMIN-K1"]
        detail = await store.get_card("ADMIN-K1")
        assert detail is not None
        assert detail["chunk_count"] == 0
        assert detail["current_version_detail"]["version"] == 1
        assert await store.get_card("MISSING") is None
    finally:
        await database.close()
