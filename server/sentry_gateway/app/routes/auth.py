"""Device enrollment, password sign-in, token issuance, and revocation.

Enrollment is deliberately two-sided: a code can only be minted from an already
authenticated session, and it can only be redeemed once, within five minutes.
There is no public self-registration path.

Password sign-in (`/password/login`) is the second door, added so onboarding a
teammate is "here is a URL, a username and a password" rather than a race
against a five-minute code. It is NOT a weaker identity: it mints exactly the
same token pair, bound to the same personal profile, so routing, audit, refresh
and revocation downstream are unchanged. Enrollment codes remain the
device-pairing path and keep working untouched.

Bootstrapping the very first device is an out-of-band operation (see
`scripts/bootstrap_device.py`), not an unauthenticated endpoint, so this API
never has a hole in it for the first caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..audit.service import AuditEvent, AuditService, Decision
from ..auth import passwords
from ..auth.tokens import (
    Audience,
    DeviceKind,
    TokenError,
    create_enrollment_code,
    hash_secret,
)
from .deps import Caller, get_token_service, require_caller

router = APIRouter(prefix="/api/auth", tags=["auth"])


class StartEnrollment(BaseModel):
    device_kind: DeviceKind
    # Naming the device up front makes the later revocation list meaningful.
    device_name: str = Field(min_length=1, max_length=100)


class EnrollmentCodeView(BaseModel):
    code: str
    expires_at: datetime


class CompleteEnrollment(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    device_name: str = Field(min_length=1, max_length=100)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    device_id: UUID
    profile_id: UUID


class DeviceView(BaseModel):
    id: UUID
    kind: DeviceKind
    name: str
    enrolled_at: datetime
    last_seen_at: datetime | None
    revoked: bool


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable.",
        )
    return pool


@router.post("/enroll/start", response_model=EnrollmentCodeView)
async def start_enrollment(
    body: StartEnrollment, request: Request, caller: Caller = Depends(require_caller)
) -> EnrollmentCodeView:
    """Mint a five-minute enrollment code for one of the caller's own devices."""
    pool = _pool(request)
    audit = AuditService(pool)
    code = create_enrollment_code(str(caller.user_id), body.device_kind)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO enrollment_codes
                    (code_hash, user_id, device_kind, expires_at, approved_by)
                VALUES ($1,$2,$3,$4,$5)
                """,
                code.code_hash,
                caller.user_id,
                body.device_kind.value,
                code.expires_at,
                caller.user_id,
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="device.enroll.start",
                    decision=Decision.ALLOWED,
                    correlation_id=f"enroll-{code.code_hash[:16]}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    target_kind="device",
                    detail=f"enrollment code issued for {body.device_kind.value}",
                ),
            )

    # The plaintext code leaves the server exactly once, here.
    return EnrollmentCodeView(code=code.code, expires_at=code.expires_at)


@router.post("/enroll/complete", response_model=TokenPair)
async def complete_enrollment(body: CompleteEnrollment, request: Request) -> TokenPair:
    """Redeem a code for a device-bound token pair. Single use."""
    pool = _pool(request)
    audit = AuditService(pool)
    tokens = get_token_service(request)
    code_hash = hash_secret(body.code)
    correlation = f"enroll-{code_hash[:16]}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT user_id, device_kind, expires_at, consumed_at
                FROM enrollment_codes WHERE code_hash = $1 FOR UPDATE
                """,
                code_hash,
            )

            now = datetime.now(timezone.utc)
            # An unknown, already-used, or expired code are all the same answer,
            # so probing cannot distinguish them.
            invalid = (
                row is None
                or row["consumed_at"] is not None
                or row["expires_at"] <= now
            )
            if invalid:
                reason = (
                    "unknown code"
                    if row is None
                    else "already consumed"
                    if row["consumed_at"] is not None
                    else "expired"
                )
                await audit.record(
                    AuditEvent(
                        action="device.enroll.complete",
                        decision=Decision.DENIED,
                        correlation_id=correlation,
                        actor_user_id=row["user_id"] if row else None,
                        target_kind="device",
                        detail=f"enrollment refused: {reason}",
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Enrollment code is invalid or has expired.",
                )

            await conn.execute(
                "UPDATE enrollment_codes SET consumed_at = now() WHERE code_hash = $1",
                code_hash,
            )

            device_id = await conn.fetchval(
                """
                INSERT INTO devices (user_id, kind, name)
                VALUES ($1,$2,$3) RETURNING id
                """,
                row["user_id"],
                row["device_kind"],
                body.device_name,
            )

            profile_id = await conn.fetchval(
                """
                SELECT id FROM profiles
                WHERE owner_user_id = $1 AND kind = 'personal' AND archived_at IS NULL
                """,
                row["user_id"],
            )
            if profile_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="User has no personal profile to bind this device to.",
                )

            audience = (
                Audience.NODE
                if row["device_kind"] == DeviceKind.EXECUTION_NODE.value
                else Audience.CLIENT
            )
            access = tokens.issue_access_token(
                user_id=str(row["user_id"]),
                device_id=str(device_id),
                profile_id=str(profile_id),
                audience=audience,
            )
            refresh = tokens.issue_refresh_token(
                user_id=str(row["user_id"]), device_id=str(device_id)
            )
            await conn.execute(
                """
                INSERT INTO refresh_tokens (token_hash, user_id, device_id, expires_at)
                VALUES ($1,$2,$3, now() + interval '30 days')
                """,
                hash_secret(refresh),
                row["user_id"],
                device_id,
            )

            await audit.record_with(
                conn,
                AuditEvent(
                    action="device.enroll.complete",
                    decision=Decision.ALLOWED,
                    correlation_id=correlation,
                    actor_user_id=row["user_id"],
                    actor_device_id=device_id,
                    profile_id=profile_id,
                    target_kind="device",
                    target_id=str(device_id),
                    detail=f"enrolled {row['device_kind']} {body.device_name!r}",
                ),
            )

    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        device_id=device_id,
        profile_id=profile_id,
    )


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(body: RefreshRequest, request: Request) -> TokenPair:
    """Rotate a refresh token. The presented token is retired as it is used, so
    replaying it fails."""
    pool = _pool(request)
    audit = AuditService(pool)
    tokens = get_token_service(request)

    try:
        claims = tokens.verify(body.refresh_token, audience=Audience.CLIENT)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    if claims.get("typ") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="An access token cannot be used to refresh.",
        )

    presented = hash_secret(body.refresh_token)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT user_id, device_id, revoked_at, rotated_to, expires_at
                FROM refresh_tokens WHERE token_hash = $1 FOR UPDATE
                """,
                presented,
            )
            now = datetime.now(timezone.utc)
            if row is None or row["revoked_at"] or row["rotated_to"] or row["expires_at"] <= now:
                await audit.record(
                    AuditEvent(
                        action="token.refresh",
                        decision=Decision.DENIED,
                        correlation_id=f"refresh-{presented[:16]}",
                        actor_user_id=UUID(claims["sub"]),
                        detail="refresh refused: token retired, revoked, or expired",
                    )
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Refresh token is no longer valid.",
                )

            device = await conn.fetchrow(
                "SELECT revoked_at FROM devices WHERE id = $1", row["device_id"]
            )
            if device is None or device["revoked_at"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Device has been revoked.",
                )

            profile_id = await conn.fetchval(
                """
                SELECT id FROM profiles
                WHERE owner_user_id = $1 AND kind = 'personal' AND archived_at IS NULL
                """,
                row["user_id"],
            )

            access = tokens.issue_access_token(
                user_id=str(row["user_id"]),
                device_id=str(row["device_id"]),
                profile_id=str(profile_id),
                audience=Audience.CLIENT,
            )
            rotated = tokens.issue_refresh_token(
                user_id=str(row["user_id"]), device_id=str(row["device_id"])
            )
            rotated_hash = hash_secret(rotated)

            await conn.execute(
                "UPDATE refresh_tokens SET rotated_to = $2 WHERE token_hash = $1",
                presented,
                rotated_hash,
            )
            await conn.execute(
                """
                INSERT INTO refresh_tokens (token_hash, user_id, device_id, expires_at)
                VALUES ($1,$2,$3, now() + interval '30 days')
                """,
                rotated_hash,
                row["user_id"],
                row["device_id"],
            )
            await conn.execute(
                "UPDATE devices SET last_seen_at = now() WHERE id = $1", row["device_id"]
            )

    return TokenPair(
        access_token=access,
        refresh_token=rotated,
        device_id=row["device_id"],
        profile_id=profile_id,
    )


class PasswordLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=passwords.PASSWORD_MAX_LENGTH)
    # Named up front for the same reason enrollment names a device: the
    # revocation list has to mean something later.
    device_name: str = Field(default="Sentry Web", min_length=1, max_length=100)


class PasswordTokenPair(TokenPair):
    """Exactly the enrollment TokenPair, plus the one extra fact a client needs.

    Subclassed rather than redefined so the two paths cannot drift: if the
    enrollment pair gains a field, this one gains it too.
    """

    must_change: bool = False


#: One message for every failure mode -- wrong password, unknown username,
#: locked, disabled. Distinguishing them at the door is how an attacker
#: enumerates accounts. It names the lockout so a genuinely locked-out teammate
#: is not left believing their password changed by itself.
_SIGN_IN_FAILED = (
    "Sign-in failed. Check your username and password. "
    "Repeated failures lock the account for 15 minutes."
)


def _throttle_key(request: Request, username: str) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) or "unknown"
    return f"{host}|{username.lower()}"


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=passwords.PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=1, max_length=passwords.PASSWORD_MAX_LENGTH)


class PasswordRecoveryStart(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class PasswordRecoveryCodeView(BaseModel):
    code: str
    expires_at: datetime


class PasswordRecoveryComplete(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=1, max_length=passwords.PASSWORD_MAX_LENGTH)


_RECOVERY_TTL = timedelta(minutes=10)
_RECOVERY_FAILED = "Recovery code is invalid or has expired."


def _require_recovery_operator(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate Server Control without granting it Sentry session power."""
    settings = getattr(request.app.state, "settings", None)
    expected = settings.load_recovery_key() if settings is not None else ""
    if not expected:
        # Hide an integration that is intentionally disabled.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    prefix = "Bearer "
    candidate = (
        authorization[len(prefix) :].strip()
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not candidate or not secrets.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Recovery operator credential required.",
        )


@router.post(
    "/password/recovery/start",
    response_model=PasswordRecoveryCodeView,
    dependencies=[Depends(_require_recovery_operator)],
)
async def start_password_recovery(
    body: PasswordRecoveryStart, request: Request
) -> PasswordRecoveryCodeView:
    """Mint one short-lived code after Server Control has verified its MFA admin."""
    pool = _pool(request)
    audit = AuditService(pool)
    username = body.username.strip().lower()
    correlation = f"pwrecover-start-{hash_secret(username)[:16]}"
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
    code_hash = hash_secret(code)
    expires_at = datetime.now(timezone.utc) + _RECOVERY_TTL

    missing = False
    user_id: UUID | None = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT c.user_id
                FROM user_passwords c
                JOIN users u ON u.id = c.user_id
                WHERE c.username = $1 AND u.disabled_at IS NULL
                FOR UPDATE OF c
                """,
                username,
            )
            if row is None:
                missing = True
            else:
                user_id = row["user_id"]
                # Only the newest unconsumed code should work. This keeps an old
                # screen or copied code from remaining a second recovery door.
                await conn.execute(
                    "UPDATE password_recovery_codes SET consumed_at = now() "
                    "WHERE user_id = $1 AND consumed_at IS NULL",
                    user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO password_recovery_codes
                        (code_hash, user_id, expires_at, approved_by)
                    VALUES ($1, $2, $3, 'server-control')
                    """,
                    code_hash,
                    user_id,
                    expires_at,
                )
                await audit.record_with(
                    conn,
                    AuditEvent(
                        action="auth.password.recovery.start",
                        decision=Decision.ALLOWED,
                        correlation_id=correlation,
                        target_kind="user",
                        target_id=str(user_id),
                        detail="single-use recovery issued by Server Control",
                    ),
                )

    if missing:
        await audit.record(
            AuditEvent(
                action="auth.password.recovery.start",
                decision=Decision.DENIED,
                correlation_id=correlation,
                target_kind="user",
                detail="recovery refused: account was unavailable",
            )
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sentry account was not found or cannot be recovered.",
        )

    return PasswordRecoveryCodeView(code=code, expires_at=expires_at)


@router.post("/password/recover", status_code=status.HTTP_204_NO_CONTENT)
async def recover_password(body: PasswordRecoveryComplete, request: Request) -> None:
    """Consume a Server Control recovery code and revoke every old session."""
    pool = _pool(request)
    audit = AuditService(pool)
    tokens = get_token_service(request)
    username = body.username.strip().lower()
    code_hash = hash_secret(body.code.strip().upper())
    correlation = f"pwrecover-{code_hash[:16]}"

    if not passwords.RECOVERY_THROTTLE.allow(_throttle_key(request, username)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many recovery attempts. Wait a minute and try again.",
        )
    problem = passwords.password_policy_error(body.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)

    revoked_devices: list[UUID] = []
    denied = False
    denied_user: UUID | None = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT r.user_id, r.expires_at, r.consumed_at, u.disabled_at,
                       c.password_hash
                FROM password_recovery_codes r
                JOIN user_passwords c ON c.user_id = r.user_id
                JOIN users u ON u.id = r.user_id
                WHERE r.code_hash = $1 AND c.username = $2
                FOR UPDATE OF r, c
                """,
                code_hash,
                username,
            )
            now = datetime.now(timezone.utc)
            invalid = (
                row is None
                or row["consumed_at"] is not None
                or row["expires_at"] <= now
                or row["disabled_at"] is not None
            )
            if invalid:
                denied = True
                denied_user = row["user_id"] if row else None
            else:
                if passwords.verify_password(row["password_hash"], body.new_password):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Choose a password different from the previous one.",
                    )
                user_id = row["user_id"]
                await conn.execute(
                    "UPDATE password_recovery_codes SET consumed_at = now() "
                    "WHERE code_hash = $1",
                    code_hash,
                )
                await conn.execute(
                    "UPDATE user_passwords SET password_hash = $2, must_change = FALSE, "
                    "failed_attempts = 0, locked_until = NULL, updated_at = now() "
                    "WHERE user_id = $1",
                    user_id,
                    passwords.hash_password(body.new_password),
                )
                device_rows = await conn.fetch(
                    "SELECT id FROM devices WHERE user_id = $1 AND revoked_at IS NULL",
                    user_id,
                )
                revoked_devices = [item["id"] for item in device_rows]
                await conn.execute(
                    "UPDATE devices SET revoked_at = now() "
                    "WHERE user_id = $1 AND revoked_at IS NULL",
                    user_id,
                )
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = now() "
                    "WHERE user_id = $1 AND revoked_at IS NULL",
                    user_id,
                )
                await audit.record_with(
                    conn,
                    AuditEvent(
                        action="auth.password.recovery.complete",
                        decision=Decision.ALLOWED,
                        correlation_id=correlation,
                        actor_user_id=user_id,
                        target_kind="user",
                        target_id=str(user_id),
                        detail="account recovered; prior devices revoked",
                    ),
                )

    if denied:
        await audit.record(
            AuditEvent(
                action="auth.password.recovery.complete",
                decision=Decision.DENIED,
                correlation_id=correlation,
                actor_user_id=denied_user,
                target_kind="user",
                target_id=str(denied_user) if denied_user else None,
                detail="recovery refused: code was unavailable",
            )
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_RECOVERY_FAILED)

    for device_id in revoked_devices:
        tokens.revoke_device(str(device_id))


@router.post("/password/login", response_model=PasswordTokenPair)
async def password_login(body: PasswordLogin, request: Request) -> PasswordTokenPair:
    """Sign in with a username and password, minting the enrollment token pair.

    Failure paths deliberately commit their database effect (the attempt
    counter, the lock) and only then raise, because raising inside the
    transaction would roll the counter back and the lockout would never fire.
    Their audit rows go through `audit.record()` on a separate connection for
    the same reason -- this is the pattern `complete_enrollment` already uses.
    """
    pool = _pool(request)
    audit = AuditService(pool)
    tokens = get_token_service(request)

    username = body.username.strip()
    device_name = body.device_name.strip() or "Sentry Web"
    # Correlates repeated attempts on one name without writing the name itself
    # into a row that is kept forever.
    correlation = f"pwlogin-{hash_secret(username.lower())[:16]}"

    # Refuse a flood BEFORE the argon2 verify: otherwise an unauthenticated
    # caller can make this route burn 64 MiB and several milliseconds per
    # request at will.
    if not passwords.LOGIN_THROTTLE.allow(_throttle_key(request, username)):
        await audit.record(
            AuditEvent(
                action="auth.password.login",
                decision=Decision.DENIED,
                correlation_id=correlation,
                target_kind="user",
                detail="sign-in refused: too many attempts from this address",
            )
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Wait a minute and try again.",
        )

    now = datetime.now(timezone.utc)
    denied: str | None = None
    denied_user: UUID | None = None
    access = refresh = ""
    device_id = profile_id = None
    must_change = False

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT c.user_id, c.password_hash, c.must_change,
                       c.failed_attempts, c.locked_until, u.disabled_at
                FROM user_passwords c
                JOIN users u ON u.id = c.user_id
                WHERE c.username = $1
                FOR UPDATE OF c
                """,
                username,
            )

            if row is None:
                # Same work as a real verify, so response timing does not
                # answer "does this account exist?".
                passwords.verify_dummy()
                denied = "no such account"
            else:
                denied_user = row["user_id"]
                locked = (
                    row["locked_until"] is not None and row["locked_until"] > now
                )
                # Verified even when the account is locked or disabled, again so
                # the three refusals take the same time.
                matched = passwords.verify_password(
                    row["password_hash"], body.password
                )

                if locked:
                    denied = "account is temporarily locked"
                elif row["disabled_at"] is not None:
                    denied = "account is disabled"
                elif not matched:
                    attempts = int(row["failed_attempts"] or 0) + 1
                    if attempts >= passwords.MAX_FAILED_ATTEMPTS:
                        # Counter resets with the lock so the next window starts
                        # clean rather than locking again on the first typo.
                        await conn.execute(
                            "UPDATE user_passwords SET failed_attempts = 0, "
                            "locked_until = $2, updated_at = now() WHERE user_id = $1",
                            row["user_id"],
                            now + timedelta(seconds=passwords.LOCKOUT_SECONDS),
                        )
                        denied = "credentials did not match; account locked"
                    else:
                        await conn.execute(
                            "UPDATE user_passwords SET failed_attempts = $2, "
                            "updated_at = now() WHERE user_id = $1",
                            row["user_id"],
                            attempts,
                        )
                        denied = "credentials did not match"
                else:
                    user_id = row["user_id"]
                    must_change = bool(row["must_change"])
                    await conn.execute(
                        "UPDATE user_passwords SET failed_attempts = 0, "
                        "locked_until = NULL, last_login_at = now(), "
                        "updated_at = now() WHERE user_id = $1",
                        user_id,
                    )
                    # Raising the cost later only helps if existing hashes get
                    # upgraded; the only moment the plaintext is available is
                    # this one.
                    if passwords.needs_rehash(row["password_hash"]):
                        await conn.execute(
                            "UPDATE user_passwords SET password_hash = $2, "
                            "updated_at = now() WHERE user_id = $1",
                            user_id,
                            passwords.hash_password(body.password),
                        )

                    # Reuse the device this browser already enrolled rather than
                    # writing a row per sign-in: the device list is a revocation
                    # surface, and a thousand identical rows make it useless.
                    device_id = await conn.fetchval(
                        "SELECT id FROM devices WHERE user_id = $1 AND kind = $2 "
                        "AND name = $3 AND revoked_at IS NULL "
                        "ORDER BY enrolled_at LIMIT 1",
                        user_id,
                        DeviceKind.BROWSER.value,
                        device_name,
                    )
                    if device_id is None:
                        device_id = await conn.fetchval(
                            "INSERT INTO devices (user_id, kind, name) "
                            "VALUES ($1,$2,$3) RETURNING id",
                            user_id,
                            DeviceKind.BROWSER.value,
                            device_name,
                        )
                    else:
                        await conn.execute(
                            "UPDATE devices SET last_seen_at = now() WHERE id = $1",
                            device_id,
                        )

                    profile_id = await conn.fetchval(
                        """
                        SELECT id FROM profiles
                        WHERE owner_user_id = $1 AND kind = 'personal'
                          AND archived_at IS NULL
                        """,
                        user_id,
                    )
                    if profile_id is None:
                        # Fail loudly rather than mint a token with no `pid`:
                        # that token would sign in and then 401 every call.
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="User has no personal profile to sign in to.",
                        )

                    access = tokens.issue_access_token(
                        user_id=str(user_id),
                        device_id=str(device_id),
                        profile_id=str(profile_id),
                        audience=Audience.CLIENT,
                    )
                    refresh = tokens.issue_refresh_token(
                        user_id=str(user_id), device_id=str(device_id)
                    )
                    await conn.execute(
                        """
                        INSERT INTO refresh_tokens
                            (token_hash, user_id, device_id, expires_at)
                        VALUES ($1,$2,$3, now() + interval '30 days')
                        """,
                        hash_secret(refresh),
                        user_id,
                        device_id,
                    )
                    await audit.record_with(
                        conn,
                        AuditEvent(
                            action="auth.password.login",
                            decision=Decision.ALLOWED,
                            correlation_id=correlation,
                            actor_user_id=user_id,
                            actor_device_id=device_id,
                            profile_id=profile_id,
                            target_kind="user",
                            target_id=str(user_id),
                            detail=f"signed in from {device_name!r}",
                        ),
                    )

    if denied is not None:
        await audit.record(
            AuditEvent(
                action="auth.password.login",
                decision=Decision.DENIED,
                correlation_id=correlation,
                actor_user_id=denied_user,
                target_kind="user",
                target_id=str(denied_user) if denied_user else None,
                detail=f"sign-in refused: {denied}",
            )
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_SIGN_IN_FAILED
        )

    return PasswordTokenPair(
        access_token=access,
        refresh_token=refresh,
        device_id=device_id,
        profile_id=profile_id,
        must_change=must_change,
    )


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, request: Request, caller: Caller = Depends(require_caller)
) -> None:
    """Change your own password. Proving the current one is required, so a
    borrowed session cannot take the account over."""
    pool = _pool(request)
    audit = AuditService(pool)
    correlation = f"pwchange-{caller.user_id}"

    problem = passwords.password_policy_error(body.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new password must be different from the current one.",
        )

    denied = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT password_hash FROM user_passwords WHERE user_id = $1 FOR UPDATE",
                caller.user_id,
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This account has no password set. Ask an operator to set one.",
                )
            if not passwords.verify_password(
                row["password_hash"], body.current_password
            ):
                denied = True
            else:
                await conn.execute(
                    "UPDATE user_passwords SET password_hash = $2, must_change = $3, "
                    "failed_attempts = 0, locked_until = NULL, updated_at = now() "
                    "WHERE user_id = $1",
                    caller.user_id,
                    passwords.hash_password(body.new_password),
                    False,
                )
                await audit.record_with(
                    conn,
                    AuditEvent(
                        action="auth.password.change",
                        decision=Decision.ALLOWED,
                        correlation_id=correlation,
                        actor_user_id=caller.user_id,
                        actor_device_id=caller.device_id,
                        profile_id=caller.profile_id,
                        target_kind="user",
                        target_id=str(caller.user_id),
                        detail="credential changed by its owner",
                    ),
                )

    if denied:
        await audit.record(
            AuditEvent(
                action="auth.password.change",
                decision=Decision.DENIED,
                correlation_id=correlation,
                actor_user_id=caller.user_id,
                actor_device_id=caller.device_id,
                profile_id=caller.profile_id,
                target_kind="user",
                target_id=str(caller.user_id),
                detail="change refused: current credential did not match",
            )
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect.",
        )


@router.get("/devices", response_model=list[DeviceView])
async def list_devices(
    request: Request, caller: Caller = Depends(require_caller)
) -> list[DeviceView]:
    """A person sees only their own devices."""
    pool = _pool(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, name, enrolled_at, last_seen_at, revoked_at
            FROM devices WHERE user_id = $1 ORDER BY enrolled_at
            """,
            caller.user_id,
        )
    return [
        DeviceView(
            id=r["id"],
            kind=DeviceKind(r["kind"]),
            name=r["name"],
            enrolled_at=r["enrolled_at"],
            last_seen_at=r["last_seen_at"],
            revoked=r["revoked_at"] is not None,
        )
        for r in rows
    ]


@router.post("/devices/{device_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: UUID, request: Request, caller: Caller = Depends(require_caller)
) -> None:
    """Revocation takes effect immediately, including for unexpired tokens."""
    pool = _pool(request)
    audit = AuditService(pool)
    tokens = get_token_service(request)

    async with pool.acquire() as conn:
        async with conn.transaction():
            owner = await conn.fetchval(
                "SELECT user_id FROM devices WHERE id = $1", device_id
            )
            # 404 rather than 403 so another user's device IDs cannot be probed.
            if owner is None or owner != caller.user_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not found."
                )

            await conn.execute(
                "UPDATE devices SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
                device_id,
            )
            await conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() WHERE device_id = $1 AND revoked_at IS NULL",
                device_id,
            )
            await audit.record_with(
                conn,
                AuditEvent(
                    action="device.revoke",
                    decision=Decision.ALLOWED,
                    correlation_id=f"revoke-{device_id}",
                    actor_user_id=caller.user_id,
                    actor_device_id=caller.device_id,
                    target_kind="device",
                    target_id=str(device_id),
                    detail="device revoked by owner",
                ),
            )

    # In-process denial so existing access tokens stop working at once, without
    # waiting for their 15-minute expiry.
    tokens.revoke_device(str(device_id))
