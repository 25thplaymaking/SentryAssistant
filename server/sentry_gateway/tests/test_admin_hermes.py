"""Admin snapshot behaviour, including the case that matters most:
a runtime that is up but has no model behind it."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.hermes import HermesInstance, HermesRuntime
from app.auth.tokens import Audience, TokenService
from app.routes import admin as admin_routes

KEY = "admin-test-signing-key-padded-past-the-32-byte-minimum"
PROFILE = UUID("11111111-1111-1111-1111-111111111111")
USER = uuid4()
DEVICE = uuid4()


class FakePool:
    """Minimal asyncpg-shaped stub; `is_admin` drives the authorization path."""

    def __init__(self, is_admin: bool) -> None:
        self._is_admin = is_admin

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *_):
                return False

        return Ctx()

    async def fetchval(self, *_args):
        return self._is_admin


def instance() -> HermesInstance:
    return HermesInstance(
        profile_id=PROFILE,
        base_url="http://hermes:8642",
        api_key="k",
        profile_name="sentry-personal",
    )


def build_app(handler, *, is_admin: bool = True) -> TestClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runtime = HermesRuntime({PROFILE: instance()}, pinned_version="0.18.2", client=client)

    app = FastAPI()
    app.include_router(admin_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = FakePool(is_admin)
    app.state.runtime = runtime

    class Settings:
        hermes_bootstrap_profile_id = str(PROFILE)

    app.state.settings = Settings()
    return TestClient(app, raise_server_exceptions=False)


def bearer() -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(USER), device_id=str(DEVICE), profile_id=str(PROFILE),
        audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


def healthy_handler(no_provider: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/capabilities":
            return httpx.Response(200, json={"features": {"kanban": True, "responses_api": True}})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.18.2"})
        if path == "/v1/chat/completions":
            if no_provider:
                return httpx.Response(
                    500,
                    json={"error": {"message": "Internal server error: No inference provider configured."}},
                )
            return httpx.Response(200, json={"choices": [{"message": {"content": "."}}]})
        return httpx.Response(404)

    return handler


class TestAuthorization:
    def test_non_admin_gets_404_not_403(self):
        """A non-admin should not learn the admin surface exists."""
        client = build_app(healthy_handler(no_provider=False), is_admin=False)
        assert client.get("/api/admin/hermes", headers=bearer()).status_code == 404

    def test_unauthenticated_is_refused(self):
        client = build_app(healthy_handler(no_provider=False))
        assert client.get("/api/admin/hermes").status_code == 401


class TestSnapshot:
    def test_healthy_runtime_with_a_provider_reports_clean(self):
        client = build_app(healthy_handler(no_provider=False))
        body = client.get("/api/admin/hermes", headers=bearer()).json()

        assert body["healthy"] is True
        assert body["can_answer"] is True
        assert body["pinned_version"] == "0.18.2"
        assert [f["severity"] for f in body["findings"]] == ["info"]

    # The case an admin most needs spelled out: everything green except the part
    # that matters.
    def test_healthy_runtime_without_a_provider_is_flagged_critical(self):
        client = build_app(healthy_handler(no_provider=True))
        body = client.get("/api/admin/hermes", headers=bearer()).json()

        assert body["healthy"] is True
        assert body["can_answer"] is False

        critical = [f for f in body["findings"] if f["severity"] == "critical"]
        assert len(critical) == 1
        assert "cannot answer" in critical[0]["summary"]
        # A finding without a remedy makes an admin guess.
        assert "OPENAI_API_KEY" in critical[0]["remedy"]

    def test_capabilities_are_reported(self):
        client = build_app(healthy_handler(no_provider=False))
        caps = client.get("/api/admin/hermes", headers=bearer()).json()["capabilities"]
        assert caps["work_board"] is True
        assert caps["sessions"] is True

    def test_registered_profile_count_is_reported(self):
        client = build_app(healthy_handler(no_provider=False))
        body = client.get("/api/admin/hermes", headers=bearer()).json()
        assert body["profiles_registered"] == 1

    def test_unreachable_runtime_is_critical(self):
        def dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = build_app(dead)
        body = client.get("/api/admin/hermes", headers=bearer()).json()

        assert body["healthy"] is False
        assert body["can_answer"] is False
        assert any(f["severity"] == "critical" for f in body["findings"])


class TestProviderProbe:
    async def test_probe_distinguishes_missing_provider_from_other_failures(self):
        """A different 500 must not be reported as 'no provider'."""

        def other_error(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": {"message": "database exploded"}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(other_error))
        runtime = HermesRuntime({PROFILE: instance()}, client=client)
        # Not a provider problem, so it does not claim to be one.
        assert await runtime.has_inference_provider(PROFILE) is True
        await client.aclose()

    async def test_unknown_profile_has_no_provider(self):
        runtime = HermesRuntime({})
        assert await runtime.has_inference_provider(PROFILE) is False
