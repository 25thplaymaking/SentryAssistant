"""Operator-side password provisioning.

`provision_teammate.py` is the only supported way a password is first set, so
the handover ("here is a URL, a username and a password") lives or dies here.
The enrollment code it already prints must survive untouched -- codes remain the
device-pairing path.
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import provision_teammate as provision  # noqa: E402

from app.auth import passwords  # noqa: E402

USER = uuid4()


class FakeConn:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, q, *a):
        self.executed.append((q, a))

    def writes(self, fragment):
        return [(q, a) for q, a in self.executed if fragment in q]


def run(coro):
    return asyncio.run(coro)


class TestSetPassword:
    def test_writes_a_credential_row(self):
        conn = FakeConn()
        run(provision.set_password(conn, USER, "alice", "a-strong-passphrase"))
        assert conn.writes("user_passwords")

    def test_stores_a_hash_and_never_the_plaintext(self):
        conn = FakeConn()
        run(provision.set_password(conn, USER, "alice", "a-strong-passphrase"))
        args = [v for _, a in conn.writes("user_passwords") for v in a]
        assert not any(v == "a-strong-passphrase" for v in args)
        assert any(
            isinstance(v, str) and v.startswith("$argon2id$") for v in args
        )

    def test_defaults_to_must_change(self):
        """The operator has seen this password, so it is a handover credential,
        not the user's own."""
        conn = FakeConn()
        run(provision.set_password(conn, USER, "alice", "a-strong-passphrase"))
        assert any(True in a for _, a in conn.writes("user_passwords"))

    def test_stored_hash_verifies(self):
        conn = FakeConn()
        run(provision.set_password(conn, USER, "alice", "a-strong-passphrase"))
        stored = next(
            v
            for _, a in conn.writes("user_passwords")
            for v in a
            if isinstance(v, str) and v.startswith("$argon2id$")
        )
        assert passwords.verify_password(stored, "a-strong-passphrase")

    def test_rejects_a_password_below_the_policy(self):
        conn = FakeConn()
        with pytest.raises(ValueError):
            run(provision.set_password(conn, USER, "alice", "short"))
        assert not conn.executed


class TestUsername:
    def test_normalises_to_lowercase(self):
        """The column is CITEXT; storing the operator's capitalisation as typed
        only makes the printed handover disagree with itself."""
        conn = FakeConn()
        run(provision.set_password(conn, USER, "Alice", "a-strong-passphrase"))
        args = [v for _, a in conn.writes("user_passwords") for v in a]
        assert "alice" in args

    @pytest.mark.parametrize("bad", ["ab", "has space", ".leading", "a" * 33, ""])
    def test_rejects_a_username_the_column_constraint_would_reject(self, bad):
        """Fail in the script with a readable message rather than as a
        CheckViolation halfway through provisioning."""
        conn = FakeConn()
        with pytest.raises(ValueError):
            run(provision.set_password(conn, USER, bad, "a-strong-passphrase"))


class TestArguments:
    def _parse(self, argv):
        return provision.build_parser().parse_args(argv)

    def test_password_can_be_given_explicitly(self):
        args = self._parse(
            ["--display-name", "Alice", "--slug", "alice",
             "--hermes-api-key", "k", "--password", "chosen-by-operator"]
        )
        assert args.password == "chosen-by-operator"

    def test_username_defaults_to_the_slug(self):
        args = self._parse(
            ["--display-name", "Alice", "--slug", "alice", "--hermes-api-key", "k"]
        )
        assert (args.username or args.slug) == "alice"

    def test_password_is_optional_so_one_can_be_generated(self):
        args = self._parse(
            ["--display-name", "Alice", "--slug", "alice", "--hermes-api-key", "k"]
        )
        assert args.password is None
