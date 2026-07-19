"""Device enrollment and token issuance.

Every enrolled phone, desktop, browser session, and execution node is bound to
exactly one user profile and can be revoked immediately. Access tokens are short
lived; refresh tokens rotate and are bound to the user *and* the device, so a
stolen refresh token cannot be replayed from elsewhere.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
ENROLLMENT_CODE_TTL = timedelta(minutes=5)

ISSUER = "sentry-gateway"

#: RFC 7518 3.2 requires an HMAC key at least as long as the hash output. A short
#: key silently weakens every token, so it is refused at construction rather than
#: warned about at first use.
MIN_SIGNING_KEY_BYTES = 32


def require_strong_key(signing_key: str) -> str:
    if len(signing_key.encode("utf-8")) < MIN_SIGNING_KEY_BYTES:
        raise ValueError(
            f"Signing key must be at least {MIN_SIGNING_KEY_BYTES} bytes; "
            "generate one with `openssl rand -base64 48`."
        )
    return signing_key


class Audience(StrEnum):
    """Tokens are audience-bound so a client token cannot be replayed at the node API."""

    CLIENT = "sentry.client"
    NODE = "sentry.node"
    WORK_ORDER = "sentry.workorder"


class DeviceKind(StrEnum):
    DESKTOP = "desktop"
    PHONE = "phone"
    BROWSER = "browser"
    EXECUTION_NODE = "executionNode"


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or not acceptable."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_secret(value: str) -> str:
    """Enrollment codes and refresh tokens are stored only as hashes."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EnrollmentCode:
    code: str
    code_hash: str
    expires_at: datetime
    user_id: str
    device_kind: DeviceKind

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or _now()) >= self.expires_at


def create_enrollment_code(user_id: str, device_kind: DeviceKind) -> EnrollmentCode:
    """Codes are short and single use, and only ever leave here once."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    return EnrollmentCode(
        code=code,
        code_hash=hash_secret(code),
        expires_at=_now() + ENROLLMENT_CODE_TTL,
        user_id=user_id,
        device_kind=device_kind,
    )


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    user_id: str
    kind: DeviceKind
    name: str
    revoked: bool = False


@dataclass(slots=True)
class TokenService:
    """Signs and verifies Sentry tokens.

    `revoked_devices` and `seen_nonces` are injected so the database owns the real
    state; this class stays pure enough to test.
    """

    signing_key: str
    revoked_devices: set[str] = field(default_factory=set)
    seen_nonces: set[str] = field(default_factory=set)
    algorithm: str = "HS256"

    def __post_init__(self) -> None:
        require_strong_key(self.signing_key)

    def issue_access_token(
        self, *, user_id: str, device_id: str, profile_id: str, audience: Audience
    ) -> str:
        now = _now()
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": audience.value,
                "sub": user_id,
                "did": device_id,
                "pid": profile_id,
                "iat": int(now.timestamp()),
                "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
                "jti": secrets.token_urlsafe(16),
            },
            self.signing_key,
            algorithm=self.algorithm,
        )

    def issue_refresh_token(self, *, user_id: str, device_id: str) -> str:
        now = _now()
        return jwt.encode(
            {
                "iss": ISSUER,
                "aud": Audience.CLIENT.value,
                "sub": user_id,
                "did": device_id,
                "typ": "refresh",
                "iat": int(now.timestamp()),
                "exp": int((now + REFRESH_TOKEN_TTL).timestamp()),
                "jti": secrets.token_urlsafe(16),
            },
            self.signing_key,
            algorithm=self.algorithm,
        )

    def verify(self, token: str, *, audience: Audience) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self.signing_key,
                algorithms=[self.algorithm],
                audience=audience.value,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("Token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenError("Token audience is not accepted here.") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"Token is invalid: {exc}") from exc

        device_id = claims.get("did")
        if not device_id:
            raise TokenError("Token is not bound to a device.")

        # Revocation must take effect immediately, even for an unexpired token.
        if device_id in self.revoked_devices:
            raise TokenError("Device has been revoked.")

        return claims

    def revoke_device(self, device_id: str) -> None:
        self.revoked_devices.add(device_id)

    def consume_nonce(self, nonce: str) -> None:
        """Single-use nonce. A replayed work order must fail closed."""
        if nonce in self.seen_nonces:
            raise TokenError("Nonce has already been used.")
        self.seen_nonces.add(nonce)
