"""Persisted per-profile runtime endpoints.

Hermes binds one process per profile (own HERMES_HOME + port + bearer key), so a
profile is only routable once the Gateway knows its endpoint. `main.build_runtime`
registers the single bootstrap profile from the environment; this module loads any
additional profiles from the `runtime_endpoints` table so a teammate can be
provisioned without an env change and restart.

The bearer key is stored as Fernet ciphertext. The key that decrypts it
(`SENTRY_RUNTIME_ENC_KEY`) lives only in the Gateway environment, never in the
database — so a database dump alone never yields a usable Hermes credential.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from cryptography.fernet import Fernet

from .hermes import HermesInstance, HermesRuntime

#: Only non-secret endpoint metadata plus the encrypted key is selected; nothing
#: here reveals a usable credential without the environment-held decryption key.
_FETCH_SQL = """
    SELECT profile_id, base_url, profile_name, api_key_encrypted
    FROM runtime_endpoints
    WHERE runtime_name = $1
"""


class EndpointCipher:
    """Symmetric encryption for endpoint bearer keys at rest.

    The key is a urlsafe-base64 Fernet key supplied through the environment.
    """

    def __init__(self, key: str | bytes) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def instance_from_row(row: Mapping, cipher: EndpointCipher) -> HermesInstance:
    """Build a HermesInstance from a persisted endpoint row, decrypting the key."""
    return HermesInstance(
        profile_id=_as_uuid(row["profile_id"]),
        base_url=row["base_url"],
        api_key=cipher.decrypt(row["api_key_encrypted"]),
        profile_name=row["profile_name"],
    )


def instances_from_rows(
    rows: Sequence[Mapping], cipher: EndpointCipher
) -> list[HermesInstance]:
    return [instance_from_row(row, cipher) for row in rows]


async def register_persisted_endpoints(
    runtime: HermesRuntime,
    pool,
    cipher: EndpointCipher,
    *,
    runtime_name: str = "hermes",
) -> int:
    """Load every persisted endpoint for `runtime_name` and register it.

    Returns the number registered. Registration is additive: a profile already
    registered (e.g. the bootstrap profile) is preserved unless a row re-registers
    it, and an unlisted profile still resolves to nothing, so routing stays
    fail-closed.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FETCH_SQL, runtime_name)
    count = 0
    for instance in instances_from_rows(rows, cipher):
        runtime.register(instance)
        count += 1
    return count


#: Single-profile variant of `_FETCH_SQL`, used to make a profile provisioned
#: after startup routable without restarting the Gateway.
_FETCH_ONE_SQL = """
    SELECT profile_id, base_url, profile_name, api_key_encrypted
    FROM runtime_endpoints
    WHERE runtime_name = $1 AND profile_id = $2
"""


async def refresh_profile_endpoint(
    runtime: HermesRuntime,
    pool,
    cipher: EndpointCipher,
    profile_id: UUID,
    *,
    runtime_name: str = "hermes",
) -> bool:
    """Load and register one profile's persisted endpoint on demand.

    Endpoints are otherwise read only at startup, so a teammate provisioned
    afterwards stayed unroutable (503) until the Gateway was hard-restarted --
    and `docker compose up -d` silently no-ops when no config changed, so that
    restart was easy to miss. Callers use this on an `UnknownProfileError` to
    pick up a newly provisioned profile.

    Routing stays fail-closed: only a row belonging to `profile_id` itself is
    registered, so a profile with no persisted endpoint remains unroutable and
    never falls through to somebody else's agent. Returns True if it registered.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FETCH_ONE_SQL, runtime_name, profile_id)
    wanted = _as_uuid(profile_id)
    for instance in instances_from_rows(rows or [], cipher):
        # Defence in depth: never trust the row to be the one we asked for.
        if instance.profile_id == wanted:
            runtime.register(instance)
            return True
    return False
