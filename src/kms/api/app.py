"""FastAPI 网关：会话凭证硬拒、只接受 question、问答主链路。"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from kms.api.admin import AdminStore, install_admin_routes
from kms.api.session import SESSION_HEADER, SessionRejected, SessionResolver
from kms.observability.metrics import observe_gateway_rejection, observe_http, render_metrics
from kms.query.pipeline import AnswerResult, QueryService
from kms.scope import ScopeError
from kms.workspace import WorkspaceRejected

logger = logging.getLogger(__name__)


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=4000)


def create_app(
    service: QueryService | Any,
    session_resolver: SessionResolver,
    *,
    admin_store: AdminStore | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="ICS Knowledge Service", version="0.1.0")

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next: Any) -> Response:
        started = time.perf_counter()
        response: Response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        observe_http(
            route=route_path,
            method=request.method,
            status_code=response.status_code,
            duration_seconds=time.perf_counter() - started,
        )
        return response

    @app.get("/metrics")
    async def metrics() -> Response:
        payload, content_type = render_metrics()
        return Response(content=payload, media_type=content_type)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if admin_store is not None and admin_token:
        install_admin_routes(app, admin_store, admin_token)

    @app.post("/v1/answer")
    async def answer(request: Request, payload: AnswerRequest) -> dict[str, Any]:
        try:
            session = await session_resolver.resolve(request.headers.get(SESSION_HEADER))
        except SessionRejected as exc:
            observe_gateway_rejection(reason="session")
            logger.warning("gateway session rejection: %s", exc)
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            result: AnswerResult = await service.answer(session, payload.question)
        except WorkspaceRejected as exc:
            observe_gateway_rejection(reason="workspace")
            logger.warning("gateway workspace rejection: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ScopeError as exc:
            observe_gateway_rejection(reason="scope")
            logger.warning("gateway scope rejection: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.as_dict()

    return app
