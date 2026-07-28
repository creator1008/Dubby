"""JWT verification prefers JWKS for asymmetric Supabase tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth import JwtVerifier
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "app_env": "local",
        "supabase_url": "https://example.supabase.co",
        "supabase_jwt_secret": "legacy-hs256-secret-not-for-es256",
        "supabase_jwt_audience": "authenticated",
        "db_backend": "supabase_rest",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_hs256_token_uses_legacy_secret() -> None:
    user_id = uuid4()
    token = jwt.encode(
        {
            "sub": str(user_id),
            "email": "admin@example.test",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "app_metadata": {"role": "admin"},
        },
        "legacy-hs256-secret-not-for-es256",
        algorithm="HS256",
    )
    user = JwtVerifier(_settings()).verify(token)
    assert user.id == user_id
    assert user.is_admin is True


def test_es256_token_uses_jwks_even_when_secret_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    user_id = uuid4()
    token = jwt.encode(
        {
            "sub": str(user_id),
            "email": "admin@example.test",
            "role": "authenticated",
            "aud": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "app_metadata": {"role": "admin"},
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    class _Key:
        key = public_key

    class _Client:
        def get_signing_key_from_jwt(self, _token: str) -> _Key:
            return _Key()

    verifier = JwtVerifier(_settings())
    monkeypatch.setattr(verifier, "_get_jwks_client", lambda: _Client())
    user = verifier.verify(token)
    assert user.id == user_id
    assert user.is_admin is True


def test_es256_ignores_misconfigured_hs256_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: forcing HS256 when secret is set broke modern Supabase JWTs."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    user_id = uuid4()
    token = jwt.encode(
        {
            "sub": str(user_id),
            "aud": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "app_metadata": {"role": "admin"},
        },
        private_key,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    class _Key:
        key = public_key

    class _Client:
        def get_signing_key_from_jwt(self, _token: str) -> _Key:
            return _Key()

    verifier = JwtVerifier(_settings(supabase_jwt_secret="definitely-wrong"))
    monkeypatch.setattr(verifier, "_get_jwks_client", lambda: _Client())
    assert verifier.verify(token).id == user_id
