"""会话签名：有效/篡改/过期/超长有效期。"""

from __future__ import annotations

import time

import pytest

from src.kms.api.session import SessionRejected, SessionSigner, SignedSessionResolver

SECRET = "s" * 40


def payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "language": "zh-CN",
        "product": ["SPOT"],
        "year": ["2024"],
        "security_level": "Secret",
        "asset": ["BTC"],
        "region": ["USA"],
        "exp": int(time.time()) + 60,
    }
    base.update(overrides)
    return base


async def test_valid_session_roundtrip() -> None:
    resolver = SignedSessionResolver(SECRET)
    scope = await resolver.resolve(SessionSigner(SECRET).sign(payload()))
    assert scope.language == "zh-CN"
    assert list(scope.product) == ["SPOT"]


async def test_tampered_signature_rejected() -> None:
    resolver = SignedSessionResolver(SECRET)
    token = SessionSigner(SECRET).sign(payload())
    body, signature = token.split(".")
    with pytest.raises(SessionRejected):
        await resolver.resolve(f"{body}.{signature[:-2]}AA")


async def test_expired_and_overlong_rejected() -> None:
    resolver = SignedSessionResolver(SECRET, max_age_seconds=300, clock=lambda: 1_000)
    signer = SessionSigner(SECRET)
    with pytest.raises(SessionRejected):
        await resolver.resolve(signer.sign(payload(exp=900)))
    with pytest.raises(SessionRejected):
        await resolver.resolve(signer.sign(payload(exp=2_000)))


async def test_missing_header_rejected() -> None:
    with pytest.raises(SessionRejected):
        await SignedSessionResolver(SECRET).resolve(None)


async def test_signer_rejects_short_secret() -> None:
    with pytest.raises(ValueError):
        SessionSigner("short")


async def test_empty_authorization_is_zero_scope_not_error() -> None:
    resolver = SignedSessionResolver(SECRET)
    scope = await resolver.resolve(
        SessionSigner(SECRET).sign(payload(product=[], year=[], security_level=[]))
    )
    ast = scope.to_ast()
    assert ast.product == ()
    assert ast.year == ()
    assert ast.security_level == ()
