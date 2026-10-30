"""Langfuse 适配器：只记元数据与脱敏摘要，不记正文/完整 Prompt（§11.6）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Observation:
    model: str
    operation: str
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    status: str
    trace_id: str | None = None


FORBIDDEN_KEYS = frozenset(
    {"prompt", "content", "query", "answer", "messages", "input", "output", "chunk_id"}
)


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in FORBIDDEN_KEYS}


class LangfuseObserver:
    """自托管 Langfuse；未安装 SDK 时为空实现，不影响主链路。"""

    def __init__(self, *, enabled: bool = False, **client_kwargs: Any) -> None:
        self._client = None
        self.enabled = enabled
        if enabled:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(**client_kwargs)
            except ImportError:
                self.enabled = False

    def log_observation(self, observation: Observation) -> dict[str, Any]:
        payload = sanitize_payload(asdict(observation))
        if self.enabled and self._client is not None:
            self._client.create_event(
                name="llm-observation",
                metadata=payload,
            )
        return payload
