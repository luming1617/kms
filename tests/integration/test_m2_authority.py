"""M2 退出证据：字典/CHECK、草稿校验、冲突队列、默认不可松。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from support.fakes import reset_database, seed_default_languages

from src.kms.authority.models import CardDraft
from src.kms.authority.repository import (
    create_card,
    enqueue_conflict,
    get_current_version,
    grouping_for_dim,
    resolve_relax_order,
)
from src.kms.authority.schema import apply_authority_schema
from src.kms.authority.validation import (
    find_conflicts,
    validate_draft,
    validate_publish_gate,
)
from src.kms.config import Settings

pytestmark = [pytest.mark.m2, pytest.mark.db]


def draft(kid: str, **overrides: object) -> CardDraft:
    base = dict(
        kid=kid,
        biz_key=f"BIZ-{kid}",
        title=f"卡片 {kid}",
        language="zh-CN",
        locale="zh-CN",
        product=("SPOT",),
        year=("2024",),
        security_level=("Secret",),
        asset=("BTC",),
        region=("USA",),
        valid_from=datetime(2024, 1, 1, tzinfo=UTC),
        content={"title": f"卡片 {kid}", "sections": [{"heading": "正文", "body": f"正文 {kid}"}]},
        topic=f"topic-{kid}",
        change_reason=f"reason-{kid}",
    )
    base.update(overrides)
    return CardDraft(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="module", autouse=True)
def prepare(settings: Settings) -> None:
    import asyncio

    async def _prepare() -> None:
        await reset_database(settings)
        await seed_default_languages(settings)
        connection = await asyncpg.connect(settings.postgres.dsn())
        try:
            await apply_authority_schema(connection)
            await apply_authority_schema(connection)
        finally:
            await connection.close()

    asyncio.run(_prepare())


async def db(settings: Settings) -> asyncpg.Connection:
    return await asyncpg.connect(settings.postgres.dsn())


async def test_dim_seeds_and_check_constraints(settings: Settings) -> None:
    connection = await db(settings)
    try:
        seeds = await connection.fetch("SELECT dim, code FROM dim_scope_value")
        assert len(seeds) >= 21
        groups = await connection.fetch("SELECT dim, grouping FROM dim_registry")
        assert {(row["dim"], row["grouping"]) for row in groups} == {
            ("product", "authorization"),
            ("year", "authorization"),
            ("security_level", "authorization"),
            ("asset", "preference"),
            ("region", "preference"),
        }
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO knowledge (kid,biz_key,title,language,product,year,security_level,
                    valid_from,locale) VALUES
                    ('BAD-1','BAD-1','bad','zh-CN','{}','{2024}','{Secret}',now(),'zh-CN')
                """
            )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO knowledge (kid,biz_key,title,language,publish_state,product,year,
                    security_level,valid_from,locale) VALUES
                    ('BAD-2','BAD-2','bad','zh-CN','NOT_A_STATE','{SPOT}','{2024}','{Secret}',
                     now(),'zh-CN')
                """
            )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO knowledge (kid,biz_key,title,language,product,year,security_level,
                    valid_from,locale) VALUES
                    ('BAD-3','BAD-3','bad','de-DE','{SPOT}','{2024}','{Secret}',now(),'de-DE')
                """
            )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO knowledge (kid,biz_key,title,language,product,year,security_level,
                    valid_from,locale) VALUES
                    ('BAD-4','BAD-4','bad','zh-CN','{SPOT}','{2024}','{Top Secret}',now(),'zh-CN')
                """
            )
    finally:
        await connection.close()


async def test_validate_draft_positive_and_negative(settings: Settings) -> None:
    connection = await db(settings)
    try:
        assert await validate_draft(connection, draft("M2-OK")) == []
        cases = {
            "M2-NO-PRODUCT": {"product": ()},
            "M2-BAD-ASSET": {"asset": ("DOGE",)},
            "M2-BAD-LEVEL": {"security_level": ("Unknown",)},
            "M2-BAD-YEAR": {"year": ("24",)},
            "M2-EXPIRED": {
                "valid_from": datetime(2020, 1, 1, tzinfo=UTC),
                "valid_to": datetime(2020, 12, 31, tzinfo=UTC),
            },
            "M2-BAD-WINDOW": {
                "valid_from": datetime(2025, 1, 2, tzinfo=UTC),
                "valid_to": datetime(2025, 1, 1, tzinfo=UTC),
            },
            "M2-BAD-LANG": {"language": "de-DE", "locale": "de-DE"},
        }
        codes: dict[str, set[str]] = {}
        for kid, overrides in cases.items():
            issues = await validate_draft(connection, draft(kid, **overrides))
            codes[kid] = {issue.code for issue in issues}
        assert "auth_dim_empty" in codes["M2-NO-PRODUCT"]
        assert "scope_value_unknown" in codes["M2-BAD-ASSET"]
        assert "security_level_invalid" in codes["M2-BAD-LEVEL"]
        assert "year_invalid" in codes["M2-BAD-YEAR"]
        assert "window_expired" in codes["M2-EXPIRED"]
        assert "window_invalid" in codes["M2-BAD-WINDOW"]
        assert "unknown_language" in codes["M2-BAD-LANG"]
    finally:
        await connection.close()


async def test_conflict_detection_goes_to_manual_queue(settings: Settings) -> None:
    connection = await db(settings)
    try:
        base = draft("M2-CONFLICT-A")
        await create_card(connection, base)
        version = await get_current_version(connection, base.kid)
        assert version is not None and version["answer_hash"]

        same_scope_diff_answer = draft(
            "M2-CONFLICT-B",
            content={
                "title": "卡片 B",
                "sections": [{"heading": "正文", "body": "完全不同的答案"}],
            },
        )
        conflicts = await find_conflicts(connection, same_scope_diff_answer)
        assert [conflict.kid for conflict in conflicts] == ["M2-CONFLICT-A"]
        await enqueue_conflict(
            connection,
            kid=same_scope_diff_answer.kid,
            language="zh-CN",
            conflicts=[conflict.kid for conflict in conflicts],
        )
        queued = await connection.fetchrow(
            "SELECT kid, conflicts, status FROM conflict_review_queue WHERE kid = $1",
            same_scope_diff_answer.kid,
        )
        assert queued is not None and queued["status"] == "PENDING"

        same_answer = draft("M2-CONFLICT-C", content=base.content)
        assert await find_conflicts(connection, same_answer) == []

        other_product = draft("M2-CONFLICT-D", product=("CONTRACT",))
        assert await find_conflicts(connection, other_product) == []

        overlap_product = draft("M2-CONFLICT-E", product=("SPOT", "CONTRACT"))
        assert [conflict.kid for conflict in await find_conflicts(connection, overlap_product)] == [
            "M2-CONFLICT-A"
        ]

        other_year = draft("M2-CONFLICT-F", year=("2025",))
        assert await find_conflicts(connection, other_year) == []

        wildcard_region = draft("M2-CONFLICT-G", region=())
        assert [conflict.kid for conflict in await find_conflicts(connection, wildcard_region)] == [
            "M2-CONFLICT-A"
        ]

        await connection.execute(
            "UPDATE knowledge SET valid_to = $2 WHERE kid = $1",
            "M2-CONFLICT-A",
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        expired_overlap = draft(
            "M2-CONFLICT-H",
            valid_from=datetime(2030, 1, 1, tzinfo=UTC),
        )
        assert await find_conflicts(connection, expired_overlap) == []

        issues, conflict_list = await validate_publish_gate(connection, same_scope_diff_answer)
        assert not issues and conflict_list
    finally:
        await connection.close()


async def test_unregistered_dim_defaults_to_authorization(settings: Settings) -> None:
    connection = await db(settings)
    try:
        await connection.execute("DELETE FROM dim_registry WHERE dim = 'region'")
        assert await grouping_for_dim(connection, "region") == "authorization"
        assert await resolve_relax_order(connection, ("asset", "region")) == ("asset",)
    finally:
        await connection.execute(
            "INSERT INTO dim_registry (dim, grouping) VALUES ('region','preference') "
            "ON CONFLICT DO NOTHING"
        )
        await connection.close()


def test_grouping_signoff_artifact(project_root: Path) -> None:
    signoff = project_root / "reports" / "m2" / "dim_registry_归组签字.md"
    assert signoff.exists()
    text = signoff.read_text(encoding="utf-8")
    for dim in ("product", "year", "security_level", "asset", "region"):
        assert dim in text
    assert "asset → region" in text
