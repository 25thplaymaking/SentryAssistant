"""Provision a Sentry teammate.

Creates a non-admin user, their personal profile, a REGISTERED per-user Hermes
runtime endpoint (encrypted key at rest), a username + password they can sign in
with immediately, and a single-use enrollment code for pairing a device. Run on
grain.silo by an operator with database access. Models `bootstrap_device.py`,
extended with endpoint registration so the teammate's chat routes to their own
agent.

The password is the normal handover: "here is a URL, a username and a password".
It is stored with `must_change` set, because the operator has seen it. The
enrollment code is unchanged and still printed -- it remains the device-pairing
path and the way back in for someone who has no password yet.

Prerequisite: the teammate's Hermes container must already be running with its
own HERMES_HOME, port, and API key, reachable from the Gateway on the compose
network. Convention:

    docker run -d --name sentry-hermes-<slug> --network sentry_sentry \\
      -e API_SERVER_ENABLED=true -e API_SERVER_KEY=<key> \\
      -e API_SERVER_HOST=0.0.0.0 -e API_SERVER_PORT=8642 \\
      -e HERMES_HOME=/home/hermes/.hermes \\
      -v /srv/sentry/repo/deploy/linux/data/hermes/<slug>:/home/hermes/.hermes \\
      sentry-hermes

Then:

    SENTRY_RUNTIME_ENC_KEY=... python3 scripts/provision_teammate.py \\
      --display-name "Alice" --slug alice --hermes-api-key <key>

The Gateway loads a profile's endpoint on demand at first turn, so no restart
is needed. (Historically it registered endpoints only at startup; note that
`docker compose up -d gateway` does NOT restart a container whose config is
unchanged, so that instruction never worked reliably anyway.)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.agent_runtime.endpoints import EndpointCipher  # noqa: E402
from app.auth import passwords  # noqa: E402
from app.auth.tokens import DeviceKind, create_enrollment_code  # noqa: E402

#: Mirrors the CHECK constraint in migrations/009_password_credentials.sql. It
#: is repeated here so a bad username fails with a sentence an operator can act
#: on, rather than as a CheckViolation halfway through provisioning.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


async def set_password(conn, user_id, username, plain, must_change: bool = True) -> str:
    """Write (or replace) the user's password credential. Returns the username
    as stored. The plaintext is hashed here and never persisted or printed by
    this function -- printing it is the caller's decision, made once."""
    normalised = str(username or "").strip().lower()
    if not _USERNAME_RE.match(normalised):
        raise ValueError(
            f"invalid username {username!r}: 3-32 characters, starting with a "
            "letter or digit, then letters, digits, dot, underscore or hyphen"
        )
    problem = passwords.password_policy_error(plain)
    if problem:
        raise ValueError(problem.lower().rstrip("."))

    await conn.execute(
        """
        INSERT INTO user_passwords (user_id, username, password_hash, must_change)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            password_hash = EXCLUDED.password_hash,
            must_change = EXCLUDED.must_change,
            failed_attempts = 0,
            locked_until = NULL,
            updated_at = now()
        """,
        user_id,
        normalised,
        passwords.hash_password(plain),
        must_change,
    )
    return normalised


async def provision(
    dsn,
    display_name,
    slug,
    hermes_base_url,
    hermes_api_key,
    enc_key,
    username=None,
    password=None,
) -> None:
    cipher = EndpointCipher(enc_key)
    profile_name = f"sentry-{slug}"
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "SELECT id FROM users WHERE display_name = $1", display_name
            )
            if user_id is None:
                user_id = await conn.fetchval(
                    "INSERT INTO users (display_name, is_admin) VALUES ($1, FALSE) RETURNING id",
                    display_name,
                )
                print(f"created user {user_id}")
            else:
                print(f"reusing user {user_id}")

            profile_id = await conn.fetchval(
                "SELECT id FROM profiles WHERE owner_user_id = $1 AND kind = 'personal' AND archived_at IS NULL",
                user_id,
            )
            if profile_id is None:
                profile_id = await conn.fetchval(
                    "INSERT INTO profiles (kind, owner_user_id, display_name, runtime_home) "
                    "VALUES ('personal', $1, $2, $3) RETURNING id",
                    user_id,
                    f"{display_name} (personal)",
                    f"/srv/sentry/runtimes/personal-{slug}",
                )
                print(f"created personal profile {profile_id}")
            else:
                print(f"reusing personal profile {profile_id}")

            await conn.execute(
                """
                INSERT INTO runtime_endpoints
                    (profile_id, runtime_name, base_url, profile_name, api_key_encrypted)
                VALUES ($1, 'hermes', $2, $3, $4)
                ON CONFLICT (profile_id) DO UPDATE SET
                    base_url = EXCLUDED.base_url,
                    profile_name = EXCLUDED.profile_name,
                    api_key_encrypted = EXCLUDED.api_key_encrypted,
                    updated_at = now()
                """,
                profile_id,
                hermes_base_url,
                profile_name,
                cipher.encrypt(hermes_api_key),
            )
            print(f"registered endpoint {hermes_base_url} for profile {profile_id}")

            # Generated when the operator did not choose one, so provisioning
            # always ends with a usable credential rather than a note to come
            # back and set one later.
            plain = password or passwords.generate_password()
            stored_username = await set_password(
                conn, user_id, username or slug, plain, must_change=True
            )
            print(f"set password credential for {stored_username!r}")

            code = create_enrollment_code(str(user_id), DeviceKind.BROWSER)
            await conn.execute(
                "INSERT INTO enrollment_codes (code_hash, user_id, device_kind, expires_at, approved_by) "
                "VALUES ($1, $2, $3, $4, $5)",
                code.code_hash,
                user_id,
                DeviceKind.BROWSER.value,
                code.expires_at,
                user_id,
            )

        print()
        print(f"  username:        {stored_username}")
        # The plaintext leaves this process exactly once, here. It is not
        # written to the database, not logged, and not recoverable afterwards.
        print(f"  password:        {plain}")
        print("                   (must be changed at first sign-in)")
        print(f"  enrollment code: {code.code}")
        print(f"  expires:         {code.expires_at.isoformat()}")
        print(f"  profile id:      {profile_id}")
        print()
        print("Give the teammate the URL, username and password; that is the whole handover.")
        print("The enrollment code is still there for pairing a device, and expires in 5 minutes.")
        print("No gateway restart is needed: the endpoint loads on demand at first turn.")
    finally:
        await conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Provision a Sentry teammate.")
    p.add_argument("--display-name", required=True)
    p.add_argument("--slug", required=True, help="short slug for the container/profile, e.g. alice")
    p.add_argument("--hermes-api-key", required=True, help="the teammate's Hermes container API key")
    p.add_argument("--hermes-base-url", default=None, help="default http://sentry-hermes-<slug>:8642")
    p.add_argument("--username", default=None, help="login name; defaults to --slug")
    p.add_argument(
        "--password",
        default=None,
        help="initial password; a strong one is generated and printed if omitted",
    )
    p.add_argument("--dsn", default=os.environ.get("SENTRY_DATABASE_URL"))
    p.add_argument("--enc-key", default=os.environ.get("SENTRY_RUNTIME_ENC_KEY"))
    return p


def main() -> None:
    p = build_parser()
    args = p.parse_args()

    if not args.dsn:
        p.error("no DSN: pass --dsn or set SENTRY_DATABASE_URL")
    if not args.enc_key:
        p.error("no encryption key: pass --enc-key or set SENTRY_RUNTIME_ENC_KEY")

    base_url = args.hermes_base_url or f"http://sentry-hermes-{args.slug}:8642"
    try:
        asyncio.run(
            provision(
                args.dsn,
                args.display_name,
                args.slug,
                base_url,
                args.hermes_api_key,
                args.enc_key,
                username=args.username,
                password=args.password,
            )
        )
    except ValueError as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
