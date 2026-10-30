"""可信会话：签名头 → SessionScope。

请求体只承载 question；授权六维与语言必须来自网关签发的会话凭证（§9.1 ①）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Protocol

from kms.scope import ScopeError, SessionScope

SESSION_HEADER = "X-ICS-Session"
SESSION_SECRET_MIN_LENGTH = 32
_CLOCK_SKEW_SECONDS = 60


class SessionRejected(Exception):
    """会话缺失、签名不合法或已过期；必须拒绝，不得回退。"""


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, TypeError) as exc:
        raise SessionRejected("会话编码不合法") from exc


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class SessionSigner:
    """网关侧签发用；服务端只验签。"""

    def __init__(self, secret: str) -> None:
        if len(secret) < SESSION_SECRET_MIN_LENGTH:
            raise ValueError(f"会话密钥至少 {SESSION_SECRET_MIN_LENGTH} 字符")
        self._secret = secret.encode("utf-8")

    def sign(self, payload: dict[str, Any]) -> str:
        body = _b64encode(_canonical(payload))
        signature = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        return f"{body}.{_b64encode(signature)}"

    def sign_scope(self, scope: SessionScope, *, ttl_seconds: int = 300) -> str:
        payload: dict[str, Any] = {
            "language": scope.language,
            "product": list(scope.product),
            "year": list(scope.year),
            "security_level": (
                scope.security_level
                if isinstance(scope.security_level, str)
                else list(scope.security_level)
            ),
            "asset": list(scope.asset),
            "region": list(scope.region),
            "exp": int(time.time()) + ttl_seconds,
        }
        return self.sign(payload)


class SessionResolver(Protocol):
    async def resolve(self, header_value: str | None) -> SessionScope: ...


class SignedSessionResolver:
    """HMAC-SHA256 签名头；默认 5 分钟有效，不接受客户端自报的过期时间。"""

    def __init__(
        self,
        secret: str,
        *,
        max_age_seconds: int = 300,
        clock: Any = time.time,
    ) -> None:
        if len(secret) < SESSION_SECRET_MIN_LENGTH:
            raise ValueError(f"会话密钥至少 {SESSION_SECRET_MIN_LENGTH} 字符")
        self._secret = secret.encode("utf-8")
        self._max_age = max_age_seconds
        self._clock = clock

    async def resolve(self, header_value: str | None) -> SessionScope:
        if not header_value:
            raise SessionRejected("缺少会话凭证")
        parts = header_value.split(".")
        if len(parts) != 2:
            raise SessionRejected("会话凭证格式不合法")
        body, signature = parts
        expected = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            raise SessionRejected("会话签名不合法")
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SessionRejected("会话载荷不合法") from exc
        if not isinstance(payload, dict):
            raise SessionRejected("会话载荷不合法")
        now = int(self._clock())
        exp = payload.get("exp")
        if not isinstance(exp, int) or exp < now - _CLOCK_SKEW_SECONDS:
            raise SessionRejected("会话已过期")
        if exp > now + self._max_age + _CLOCK_SKEW_SECONDS:
            raise SessionRejected("会话有效期超出上限")
        language = payload.get("language")
        if not isinstance(language, str) or not language:
            raise SessionRejected("会话缺少语言")
        try:
            return SessionScope(
                language=language,
                product=payload.get("product") or (),
                year=payload.get("year") or (),
                security_level=payload.get("security_level") or (),
                asset=payload.get("asset") or (),
                region=payload.get("region") or (),
            )
        except ScopeError as exc:
            raise SessionRejected(f"会话 scope 不合法：{exc}") from exc
