"""Password credentials.

Onboarding a teammate used to be a device-pairing dance: an operator minted a
five-minute enrollment code and the teammate had to redeem it before it went
stale. That is the right primitive for pairing a *device*, and it stays. It is
the wrong primitive for "here is a URL, a username and a password", so this
module supplies the second one.

Hashing is argon2id, which is already a declared dependency of this service
(``pyproject.toml`` / ``Dockerfile``) and is the current password-hashing
recommendation (RFC 9106). Two properties matter more than the exact cost:

* the stored value is a PHC string, so the algorithm, its cost parameters and
  the per-hash salt all travel *with* the hash. Raising the cost later applies
  to new and re-verified hashes without invalidating a single existing one --
  which is the only way a cost increase ever actually happens.
* verification is constant-time inside argon2, and an unknown username still
  pays for a verify (``verify_dummy``) so response timing does not answer the
  question "does this account exist?".

Plaintext is never stored, never logged, and never placed in an audit detail
(``app/audit/service.py`` redacts details mentioning a credential anyway).
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Twelve characters is the floor the change route enforces. It is short enough
#: that a memorable passphrase clears it and long enough that, combined with the
#: lockout below, online guessing is hopeless.
PASSWORD_MIN_LENGTH = 12

#: Argon2 hashes the whole input, so a huge body is a cheap way to burn CPU on
#: an unauthenticated route. Cap it well above any real passphrase.
PASSWORD_MAX_LENGTH = 1024

#: Five consecutive failures locks the account for fifteen minutes. Five
#: tolerates fat fingers and a stale saved password; fifteen minutes caps an
#: attacker at twenty guesses an hour (meaningless against a 12-character
#: secret) while letting a locked-out teammate recover without an operator.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

#: Explicit rather than inherited from the library so a future argon2-cffi
#: default change cannot silently move the cost of this deployment. These are
#: the RFC 9106 second recommended option (64 MiB, t=3).
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

#: Verified against when the username does not exist, so that path does the same
#: work as a wrong password. The value is unreachable: nothing can present it.
_DUMMY_HASH = _HASHER.hash("sentry:no-such-account:" + secrets.token_hex(16))

#: Unambiguous alphabet for generated passwords: no 0/O, 1/l/I. An operator
#: reads these aloud or pastes them into a chat window, and a transcription
#: error looks exactly like a wrong password.
_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_GENERATED_LENGTH = 20


def hash_password(plain: str) -> str:
    """Return the PHC string to store. Raises on a non-string input."""
    if not isinstance(plain, str):
        raise TypeError("password must be a string")
    return _HASHER.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """True when ``plain`` matches. Never raises: a corrupt row fails closed."""
    if not stored_hash or not isinstance(plain, str):
        return False
    try:
        return bool(_HASHER.verify(stored_hash, plain))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensive; must not 500 the route
        return False


def verify_dummy() -> None:
    """Burn the same work as a real verify, for accounts that do not exist."""
    try:
        _HASHER.verify(_DUMMY_HASH, "sentry:wrong")
    except Exception:
        pass


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash was made with weaker parameters than current."""
    if not stored_hash:
        return False
    try:
        return bool(_HASHER.check_needs_rehash(stored_hash))
    except Exception:
        return False


def password_policy_error(new_password: str) -> str | None:
    """Return a human reason the password is unacceptable, or None."""
    if not isinstance(new_password, str) or not new_password.strip():
        return "A password is required."
    if len(new_password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(new_password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    return None


def generate_password(length: int = _GENERATED_LENGTH) -> str:
    """A strong password an operator can hand over, printed exactly once."""
    length = max(length, PASSWORD_MIN_LENGTH)
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


@dataclass
class AttemptThrottle:
    """A sliding-window rate limit for sign-in attempts.

    Per-account lockout is the control that stops guessing; this is the control
    that stops an unauthenticated caller from forcing unbounded 64 MiB argon2
    computations, and it has to refuse *before* the verify to do that. In-process
    and therefore per-gateway-replica, which is honest for a single-container
    deployment and is a floor, not a ceiling, if that ever changes.
    """

    limit: int = 10
    window_seconds: int = 60
    _hits: dict[str, deque[float]] = field(default_factory=dict, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(moment)
            # Bound memory: drop keys that have gone quiet.
            if len(self._hits) > 4096:
                for stale in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(stale, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


#: Shared by the login route. Module-level so it survives across requests.
LOGIN_THROTTLE = AttemptThrottle()
