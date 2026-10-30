"""按语言路由到各自的 QueryService（单进程多 workspace）。"""

from __future__ import annotations

from collections.abc import Mapping

from kms.query.pipeline import AnswerResult, QueryService
from kms.scope import SessionScope
from kms.workspace import WorkspaceRejected


class MultiWorkspaceQueryService:
    def __init__(self, services: Mapping[str, QueryService]) -> None:
        self._services = dict(services)

    async def answer(self, session: SessionScope, question: str) -> AnswerResult:
        service = self._services.get(session.language)
        if service is None:
            raise WorkspaceRejected(f"语言未装配：{session.language!r}")
        return await service.answer(session, question)
