"""L0：workspace={lang} 的唯一路由入口（§三）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

import asyncpg
from lightrag.utils import validate_workspace

from kms.db import Database

WORKSPACE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS workspace_registry (
    lang text NOT NULL PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT true,
    embedding_model text NOT NULL,
    embedding_dim int NOT NULL,
    corpus_generation bigint NOT NULL DEFAULT 0,
    next_boundary timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class WorkspaceRejected(LookupError):
    """语言未登记、已停用或语法非法；网关必须拒收且不回退默认语言。"""


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    lang: str
    enabled: bool
    embedding_model: str
    embedding_dim: int
    corpus_generation: int
    next_boundary: datetime | None


def validate_language_syntax(lang: str) -> None:
    if not lang or lang != lang.strip():
        raise WorkspaceRejected(f"非法语言：{lang!r}")
    try:
        validate_workspace(lang)
    except ValueError as exc:
        raise WorkspaceRejected(f"非法语言：{lang!r}") from exc


async def ensure_registry_schema(connection: asyncpg.Connection) -> None:
    await connection.execute(WORKSPACE_REGISTRY_DDL)


async def seed_language(
    connection: asyncpg.Connection, *, lang: str, embedding_model: str, embedding_dim: int
) -> None:
    validate_language_syntax(lang)
    await connection.execute(
        """
        INSERT INTO workspace_registry (lang, embedding_model, embedding_dim)
        VALUES ($1, $2, $3)
        ON CONFLICT (lang) DO NOTHING
        """,
        lang,
        embedding_model,
        embedding_dim,
    )


class WorkspaceRegistry:
    """精确匹配、不回退、不做大小写归一（zh-cn != zh-CN）。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def resolve(self, lang: str) -> WorkspaceRecord:
        validate_language_syntax(lang)
        row = await self._db.fetchrow("SELECT * FROM workspace_registry WHERE lang = $1", lang)
        if row is None:
            raise WorkspaceRejected(f"未登记的语言：{lang!r}")
        if not row["enabled"]:
            raise WorkspaceRejected(f"语言已停用：{lang!r}")
        return WorkspaceRecord(
            lang=row["lang"],
            enabled=row["enabled"],
            embedding_model=row["embedding_model"],
            embedding_dim=row["embedding_dim"],
            corpus_generation=row["corpus_generation"],
            next_boundary=row["next_boundary"],
        )

    async def list_all(self) -> list[WorkspaceRecord]:
        rows = await self._db.fetch("SELECT * FROM workspace_registry ORDER BY lang")
        return [
            WorkspaceRecord(
                lang=row["lang"],
                enabled=row["enabled"],
                embedding_model=row["embedding_model"],
                embedding_dim=row["embedding_dim"],
                corpus_generation=row["corpus_generation"],
                next_boundary=row["next_boundary"],
            )
            for row in rows
        ]

    async def bump_generation(self, lang: str, connection: asyncpg.Connection | None = None) -> int:
        sql = (
            "UPDATE workspace_registry SET corpus_generation = corpus_generation + 1, "
            "updated_at = now() WHERE lang = $1 RETURNING corpus_generation"
        )
        if connection is not None:
            return int(await connection.fetchval(sql, lang))
        return int(await self._db.fetchval(sql, lang))

    async def refresh_next_boundary(
        self, lang: str, connection: asyncpg.Connection | None = None
    ) -> datetime | None:
        """按该语言 PUBLISHED 卡片重算最早未来边界；knowledge 未建时返回 None。"""
        sql = """
        SELECT min(boundary) FROM (
            SELECT valid_from AS boundary FROM knowledge
            WHERE language = $1 AND publish_state = 'PUBLISHED' AND valid_from > now()
            UNION ALL
            SELECT valid_to AS boundary FROM knowledge
            WHERE language = $1 AND publish_state = 'PUBLISHED'
              AND valid_to IS NOT NULL AND valid_to > now()
        ) AS boundaries
        """
        try:
            if connection is not None:
                boundary = await connection.fetchval(sql, lang)
            else:
                boundary = await self._db.fetchval(sql, lang)
        except asyncpg.exceptions.UndefinedTableError:
            boundary = None
        update_sql = (
            "UPDATE workspace_registry SET next_boundary = $2, updated_at = now() WHERE lang = $1"
        )
        if connection is not None:
            await connection.execute(update_sql, lang, boundary)
        else:
            await self._db.execute(update_sql, lang, boundary)
        return cast("datetime | None", boundary)

    async def advance_due_boundary(self, lang: str) -> bool:
        """有效期边界到点：先推进代次，再返回是否推进过（§7.3）。"""
        due = await self._db.fetchval(
            """
            UPDATE workspace_registry
            SET corpus_generation = corpus_generation + 1, updated_at = now()
            WHERE lang = $1 AND next_boundary IS NOT NULL AND next_boundary <= now()
            RETURNING corpus_generation
            """,
            lang,
        )
        if due is None:
            return False
        await self.refresh_next_boundary(lang)
        return True
