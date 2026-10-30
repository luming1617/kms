"""按语言冻结发布队列（§12.2）；读写都不影响其他语言。"""

from __future__ import annotations

import asyncpg


async def is_frozen(connection: asyncpg.Connection, language: str) -> bool:
    value = await connection.fetchval(
        "SELECT frozen FROM workspace_publish_freeze WHERE lang = $1", language
    )
    return bool(value)


async def freeze_language(connection: asyncpg.Connection, language: str, reason: str) -> None:
    await connection.execute(
        """
        INSERT INTO workspace_publish_freeze (lang, frozen, reason)
        VALUES ($1, true, $2)
        ON CONFLICT (lang) DO UPDATE SET frozen = true, reason = $2, updated_at = now()
        """,
        language,
        reason,
    )


async def unfreeze_language(connection: asyncpg.Connection, language: str) -> None:
    await connection.execute(
        """
        UPDATE workspace_publish_freeze
        SET frozen = false, reason = NULL, updated_at = now()
        WHERE lang = $1
        """,
        language,
    )
