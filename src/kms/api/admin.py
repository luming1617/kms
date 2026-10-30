"""只读管理面：只查权威层（knowledge / knowledge_version / chunk_filter）。"""

from __future__ import annotations

import hmac
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Request

from kms.db import Database


class AdminStore(Protocol):
    async def list_cards(
        self, *, language: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...
    async def get_card(self, kid: str) -> dict[str, Any] | None: ...


class AuthorityAdminStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def list_cards(
        self, *, language: str | None, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        async with self._db.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT kid, biz_key, title, language, publish_state, current_version,
                       product, year, security_level, asset, region,
                       valid_from, valid_to, updated_at
                FROM knowledge
                WHERE ($1::text IS NULL OR language = $1)
                ORDER BY kid LIMIT $2 OFFSET $3
                """,
                language,
                limit,
                offset,
            )
        result = []
        for row in rows:
            item = dict(row)
            for dim in ("product", "year", "security_level", "asset", "region"):
                item[dim] = list(item[dim])
            result.append(item)
        return result

    async def get_card(self, kid: str) -> dict[str, Any] | None:
        async with self._db.acquire() as connection:
            card = await connection.fetchrow(
                """
                SELECT kid, biz_key, title, schema_version, current_version, publish_state,
                       language, product, year, security_level, asset, region,
                       valid_from, valid_to, updated_at
                FROM knowledge WHERE kid = $1
                """,
                kid,
            )
            if card is None:
                return None
            version = await connection.fetchrow(
                """
                SELECT version, answer_hash, topic, content, published_at
                FROM knowledge_version WHERE kid = $1 AND version = $2
                """,
                kid,
                card["current_version"],
            )
            chunk_count = await connection.fetchval(
                "SELECT count(*) FROM chunk_filter WHERE kid = $1 AND kid_version = $2",
                kid,
                card["current_version"],
            )
        item = dict(card)
        for dim in ("product", "year", "security_level", "asset", "region"):
            item[dim] = list(item[dim])
        item["current_version_detail"] = dict(version) if version else None
        item["chunk_count"] = int(chunk_count or 0)
        return item


def install_admin_routes(app: FastAPI, store: AdminStore, token: str) -> None:
    def authorize(request: Request) -> None:
        header = request.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(header, expected):
            raise HTTPException(status_code=401, detail="management token required")

    @app.get("/v1/admin/cards")
    async def list_cards(
        request: Request,
        language: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        authorize(request)
        return await store.list_cards(language=language, limit=limit, offset=offset)

    @app.get("/v1/admin/cards/{kid}")
    async def get_card(kid: str, request: Request) -> dict[str, Any]:
        authorize(request)
        card = await store.get_card(kid)
        if card is None:
            raise HTTPException(status_code=404, detail="card not found")
        return card
