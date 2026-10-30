"""L0 网关：语言解析是唯一入口，非法/停用语言硬拒（§3.1）。"""

from __future__ import annotations

from kms.workspace import WorkspaceRecord, WorkspaceRegistry, WorkspaceRejected


class GatewayRejection(Exception):
    def __init__(self, reason: str, status_code: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


async def resolve_request_workspace(registry: WorkspaceRegistry, language: str) -> WorkspaceRecord:
    try:
        return await registry.resolve(language)
    except WorkspaceRejected as exc:
        raise GatewayRejection(str(exc), status_code=400) from exc
