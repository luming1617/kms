"""只读管理面：token 校验、列表与详情。"""

from __future__ import annotations

from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient

from src.kms.api.app import create_app
from src.kms.scope import SessionScope


class FakeResolver:
    async def resolve(self, _header):
        return SessionScope(
            language="zh-CN",
            product=("SPOT",),
            year=("2024",),
            security_level="Secret",
        )


class FakeQueryService:
    async def answer(self, _session, _question):
        return SimpleNamespace()


class FakeStore:
    async def list_cards(self, *, language, limit, offset):
        return [{"kid": "K1", "language": language or "zh-CN"}][:limit]

    async def get_card(self, kid):
        return {"kid": kid} if kid == "K1" else None


async def test_admin_routes_require_token() -> None:
    app = create_app(FakeQueryService(), FakeResolver(), admin_store=FakeStore(), admin_token="t")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/v1/admin/cards")
        assert denied.status_code == 401
        allowed = await client.get("/v1/admin/cards", headers={"Authorization": "Bearer t"})
        assert allowed.status_code == 200
        assert allowed.json()[0]["kid"] == "K1"
        detail = await client.get("/v1/admin/cards/K1", headers={"Authorization": "Bearer t"})
        assert detail.status_code == 200
        missing = await client.get("/v1/admin/cards/NOPE", headers={"Authorization": "Bearer t"})
        assert missing.status_code == 404
