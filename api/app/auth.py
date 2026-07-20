"""Supabase JWT authentication boundary.

Every request except health checks must carry `Authorization: Bearer <jwt>`
issued by Supabase Auth. Two verification modes are supported:

- HS256 with the project's legacy JWT secret (``SUPABASE_JWT_SECRET``).
- Asymmetric keys (RS256/ES256) fetched from the project's JWKS endpoint,
  used automatically when no secret is configured.

The API trusts only the token; it never trusts client-supplied user ids.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .errors import UnauthorizedError

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

_ASYMMETRIC_ALGS = ["RS256", "ES256"]


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    role: str
    is_admin: bool = False


class JwtVerifier:
    """Verifies Supabase access tokens; caches the JWKS client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks_client: jwt.PyJWKClient | None = None

    def _get_jwks_client(self) -> jwt.PyJWKClient:
        if self._jwks_client is None:
            if not self._settings.supabase_url:
                raise UnauthorizedError("Auth is not configured on this server")
            self._jwks_client = jwt.PyJWKClient(
                self._settings.supabase_jwks_url,
                cache_keys=True,
                lifespan=300,
            )
        return self._jwks_client

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            if self._settings.supabase_jwt_secret:
                claims = jwt.decode(
                    token,
                    self._settings.supabase_jwt_secret,
                    algorithms=["HS256"],
                    audience=self._settings.supabase_jwt_audience,
                    options={"require": ["exp", "sub"]},
                )
            else:
                signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=_ASYMMETRIC_ALGS,
                    audience=self._settings.supabase_jwt_audience,
                    options={"require": ["exp", "sub"]},
                )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError("Token expired") from exc
        except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
            logger.debug("JWT rejected: %s", exc)
            raise UnauthorizedError("Invalid token") from exc

        try:
            user_id = UUID(str(claims["sub"]))
        except (ValueError, KeyError) as exc:
            raise UnauthorizedError("Invalid token subject") from exc

        app_metadata = claims.get("app_metadata") or {}
        return AuthenticatedUser(
            id=user_id,
            email=claims.get("email"),
            role=str(claims.get("role", "authenticated")),
            is_admin=(
                app_metadata.get("role") == "admin"
                or "admin" in (app_metadata.get("roles") or [])
            ),
        )


def get_verifier(request: Request) -> JwtVerifier:
    verifier: JwtVerifier | None = getattr(request.app.state, "jwt_verifier", None)
    if verifier is None:
        verifier = JwtVerifier(get_settings())
        request.app.state.jwt_verifier = verifier
    return verifier


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[JwtVerifier, Depends(get_verifier)],
) -> AuthenticatedUser:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Missing bearer token")
    return verifier.verify(credentials.credentials)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> AuthenticatedUser:
    if not user.is_admin:
        raise UnauthorizedError("Administrator access required")
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(get_admin_user)]
