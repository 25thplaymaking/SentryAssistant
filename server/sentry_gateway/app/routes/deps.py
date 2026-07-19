"""Request authentication.

Every non-health route resolves a caller here. A request without a valid,
audience-correct, non-revoked device token never reaches a handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from ..auth.tokens import Audience, TokenError, TokenService


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: UUID
    device_id: UUID
    profile_id: UUID


def get_token_service(request: Request) -> TokenService:
    service: TokenService | None = getattr(request.app.state, "tokens", None)
    if service is None:
        # Fail closed: a missing signing key must never mean "allow everyone".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway is not configured to verify tokens.",
        )
    return service


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def require_caller(
    authorization: str | None = Header(default=None),
    tokens: TokenService = Depends(get_token_service),
) -> Caller:
    token = _bearer(authorization)
    try:
        claims = tokens.verify(token, audience=Audience.CLIENT)
    except TokenError as exc:
        # The reason is safe to return: it distinguishes expired from revoked
        # without revealing anything about other users or devices.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        return Caller(
            user_id=UUID(claims["sub"]),
            device_id=UUID(claims["did"]),
            profile_id=UUID(claims["pid"]),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token claims are malformed.",
        ) from exc


def require_node(
    authorization: str | None = Header(default=None),
    tokens: TokenService = Depends(get_token_service),
) -> Caller:
    """Execution nodes use a separate audience, so a client token cannot be
    replayed against node endpoints."""
    token = _bearer(authorization)
    try:
        claims = tokens.verify(token, audience=Audience.NODE)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    try:
        return Caller(
            user_id=UUID(claims["sub"]),
            device_id=UUID(claims["did"]),
            profile_id=UUID(claims["pid"]),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token claims are malformed.",
        ) from exc
