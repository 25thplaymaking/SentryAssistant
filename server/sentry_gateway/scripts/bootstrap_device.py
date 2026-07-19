"""Bootstrap the first user, personal profile, and enrollment code.

Enrolling a device normally requires an already-authenticated session, which
leaves the very first device with nowhere to start. Rather than punching an
unauthenticated hole in the API, that one case is handled here: an operator with
database access runs this on the server.

    python3 scripts/bootstrap_device.py --display-name "Bryce" --device-kind desktop

It prints an enrollment code to redeem against POST /api/auth/enroll/complete.
The code is valid for five minutes and can be used once.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

from app.auth.tokens import DeviceKind, create_enrollment_code  # noqa: E402


async def bootstrap(dsn: str, display_name: str, device_kind: DeviceKind) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            user_id = await conn.fetchval(
                "SELECT id FROM users WHERE display_name = $1", display_name
            )
            if user_id is None:
                user_id = await conn.fetchval(
                    """
                    INSERT INTO users (display_name, is_admin)
                    VALUES ($1, TRUE) RETURNING id
                    """,
                    display_name,
                )
                print(f"created user {user_id}")
            else:
                print(f"reusing user {user_id}")

            profile_id = await conn.fetchval(
                """
                SELECT id FROM profiles
                WHERE owner_user_id = $1 AND kind = 'personal' AND archived_at IS NULL
                """,
                user_id,
            )
            if profile_id is None:
                # Each profile needs its own runtime home; two profiles must never
                # share one writable runtime data directory.
                profile_id = await conn.fetchval(
                    """
                    INSERT INTO profiles (kind, owner_user_id, display_name, runtime_home)
                    VALUES ('personal', $1, $2, $3) RETURNING id
                    """,
                    user_id,
                    f"{display_name} (personal)",
                    f"/srv/sentry/runtimes/personal-{user_id}",
                )
                print(f"created personal profile {profile_id}")
            else:
                print(f"reusing personal profile {profile_id}")

            code = create_enrollment_code(str(user_id), device_kind)
            await conn.execute(
                """
                INSERT INTO enrollment_codes
                    (code_hash, user_id, device_kind, expires_at, approved_by)
                VALUES ($1,$2,$3,$4,$5)
                """,
                code.code_hash,
                user_id,
                device_kind.value,
                code.expires_at,
                user_id,
            )

        print()
        print(f"  enrollment code: {code.code}")
        print(f"  expires:         {code.expires_at.isoformat()}")
        print(f"  profile id:      {profile_id}")
        print()
        print("Redeem with POST /api/auth/enroll/complete")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--device-kind",
        default="desktop",
        choices=[k.value for k in DeviceKind],
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("SENTRY_DATABASE_URL"),
        help="Defaults to $SENTRY_DATABASE_URL.",
    )
    args = parser.parse_args()

    if not args.dsn:
        parser.error("no DSN: pass --dsn or set SENTRY_DATABASE_URL")

    asyncio.run(bootstrap(args.dsn, args.display_name, DeviceKind(args.device_kind)))


if __name__ == "__main__":
    main()
