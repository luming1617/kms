"""CLI 装配：运行时、验签器与关闭顺序。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.kms.cli as cli
from src.kms.config import Settings


async def test_serve_wires_runtime_and_closes(monkeypatch) -> None:
    base = Settings.from_env()
    settings = replace(
        base,
        session_secret="s" * 40,
        embedding_base_url="http://llm/v1",
        llm=replace(base.llm, base_url="http://llm/v1"),
        langfuse_enabled=False,
        otel_endpoint=None,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    runtime = SimpleNamespace(query_service=object(), aclose=AsyncMock())
    built: dict[str, object] = {}

    async def fake_build(*_args, **kwargs):
        built.update(kwargs)
        return runtime

    monkeypatch.setattr(cli, "build_runtime", fake_build)
    app = SimpleNamespace()
    monkeypatch.setattr(cli, "create_app", lambda service, resolver, **kwargs: app)

    class FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(cli.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(
        cli.uvicorn,
        "Config",
        lambda app_obj, **kwargs: SimpleNamespace(app=app_obj, **kwargs),
    )
    await cli._serve()
    assert built
    runtime.aclose.assert_awaited()
