"""Per-profile Hermes endpoints load from the DB and route fail-closed.

Hermes binds one process per profile (own HERMES_HOME + port + bearer key), so
the Gateway resolves a distinct endpoint per profile. These persisted endpoints
are how a profile becomes routable without an env change + restart. The bearer
key is stored as Fernet ciphertext; the key that decrypts it lives only in the
Gateway environment.
"""

from uuid import UUID

import pytest
from cryptography.fernet import Fernet

from app.agent_runtime.endpoints import (
    EndpointCipher,
    instances_from_rows,
    register_persisted_endpoints,
)
from app.agent_runtime.hermes import HermesInstance, HermesRuntime, UnknownProfileError

KEY = Fernet.generate_key().decode()
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _row(profile_id, base_url, name, api_key, cipher):
    return {
        "profile_id": profile_id,
        "base_url": base_url,
        "profile_name": name,
        "api_key_encrypted": cipher.encrypt(api_key),
    }


class FakePool:
    """asyncpg-shaped stub whose connection returns the given rows from fetch()."""

    def __init__(self, rows):
        self._rows = rows

    def acquire(self):
        rows = self._rows

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetch(self, *_args):
                        return rows

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


class TestCipher:
    def test_round_trip(self):
        cipher = EndpointCipher(KEY)
        assert cipher.decrypt(cipher.encrypt("sk-secret")) == "sk-secret"

    def test_ciphertext_hides_plaintext(self):
        token = EndpointCipher(KEY).encrypt("sk-secret")
        assert "sk-secret" not in token


class TestInstancesFromRows:
    def test_decrypts_and_maps_fields(self):
        cipher = EndpointCipher(KEY)
        rows = [_row(PROFILE_A, "http://hermes-a:8642", "sentry-a", "key-a", cipher)]
        [inst] = instances_from_rows(rows, cipher)
        assert inst.profile_id == PROFILE_A
        assert inst.base_url == "http://hermes-a:8642"
        assert inst.profile_name == "sentry-a"
        assert inst.api_key == "key-a"

    def test_accepts_string_profile_id(self):
        cipher = EndpointCipher(KEY)
        rows = [_row(str(PROFILE_A), "http://h:8642", "n", "k", cipher)]
        [inst] = instances_from_rows(rows, cipher)
        assert inst.profile_id == PROFILE_A


class TestRegisterPersistedEndpoints:
    async def test_registers_each_profile_and_routes(self):
        cipher = EndpointCipher(KEY)
        rows = [
            _row(PROFILE_A, "http://hermes-a:8642", "sentry-a", "key-a", cipher),
            _row(PROFILE_B, "http://hermes-b:8642", "sentry-b", "key-b", cipher),
        ]
        runtime = HermesRuntime()
        count = await register_persisted_endpoints(runtime, FakePool(rows), cipher)
        assert count == 2
        assert runtime._instance(PROFILE_A).api_key == "key-a"
        assert runtime._instance(PROFILE_B).base_url == "http://hermes-b:8642"

    async def test_unregistered_profile_still_fails_closed(self):
        cipher = EndpointCipher(KEY)
        rows = [_row(PROFILE_A, "http://hermes-a:8642", "sentry-a", "key-a", cipher)]
        runtime = HermesRuntime()
        await register_persisted_endpoints(runtime, FakePool(rows), cipher)
        with pytest.raises(UnknownProfileError):
            runtime._instance(PROFILE_B)

    async def test_preserves_prior_bootstrap_registration(self):
        cipher = EndpointCipher(KEY)
        runtime = HermesRuntime()
        runtime.register(
            HermesInstance(
                profile_id=PROFILE_B,
                base_url="http://boot:8642",
                api_key="boot",
                profile_name="boot",
            )
        )
        rows = [_row(PROFILE_A, "http://hermes-a:8642", "sentry-a", "key-a", cipher)]
        await register_persisted_endpoints(runtime, FakePool(rows), cipher)
        assert runtime._instance(PROFILE_A).api_key == "key-a"
        assert runtime._instance(PROFILE_B).api_key == "boot"
