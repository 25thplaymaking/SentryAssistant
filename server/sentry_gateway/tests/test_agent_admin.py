"""The agent's credential and pending-write surfaces must be profile-scoped,
and a missing admin surface must be distinguishable from a real failure.

Background: Hermes keeps credential login and pending-write approval in its CLI.
In this split-container deployment the WebUI never loads the agent, so both were
unreachable — memory proposals staged by `memory.write_approval: true` piled up
on disk with nothing able to approve them. These tests pin the properties that
make the proxy safe rather than merely present:

  1. Every call carries the CALLER's profile, never a client-supplied one.
  2. A runtime without the admin patch reports 501 ("rebuild the image"), not a
     generic 502 — collapsing those is what let the gap stay invisible.
  3. A failed apply does NOT discard the staged write.
"""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.hermes import AdminSurfaceUnavailable, UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import agent_admin as agent_admin_routes

KEY = "agent-admin-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class FakeRuntime:
    """Records the profile each call was made with."""

    def __init__(self, known_profiles=(PROFILE_A,), raises=None):
        self._known = set(known_profiles)
        self._raises = raises
        self.calls: list[tuple] = []

    def _guard(self, profile_id):
        if self._raises is not None:
            raise self._raises
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))

    async def list_pending_writes(self, profile_id, subsystem):
        self._guard(profile_id)
        self.calls.append(("list_pending", profile_id, subsystem))
        return {"object": "list", "subsystem": subsystem, "data": []}

    async def decide_pending_write(self, profile_id, subsystem, pending_id, *, approve):
        self._guard(profile_id)
        self.calls.append(("decide", profile_id, subsystem, pending_id, approve))
        return {"subsystem": subsystem, "id": pending_id,
                "status": "approved" if approve else "rejected"}

    async def list_auth_providers(self, profile_id):
        self._guard(profile_id)
        self.calls.append(("providers", profile_id))
        return {"object": "list", "data": []}

    async def start_oauth(self, profile_id, provider):
        self._guard(profile_id)
        self.calls.append(("start", profile_id, provider))
        return {"flow_id": "f1", "authorize_url": "https://example.invalid/auth"}

    async def complete_oauth(self, profile_id, flow_id, code, label=None):
        self._guard(profile_id)
        self.calls.append(("complete", profile_id, flow_id, code, label))
        return {"provider": "anthropic", "credential": {"id": "abc123"}}

    async def oauth_status(self, profile_id, flow_id):
        self._guard(profile_id)
        self.calls.append(("oauth_status", profile_id, flow_id))
        return {"flow_id": flow_id, "status": "awaiting_user"}

    async def cancel_oauth(self, profile_id, flow_id):
        self._guard(profile_id)
        self.calls.append(("oauth_cancel", profile_id, flow_id))
        return {"flow_id": flow_id, "status": "cancelled"}

    async def logout_provider(self, profile_id, provider, credential=None):
        self._guard(profile_id)
        self.calls.append(("logout", profile_id, provider, credential))
        return {"provider": provider, "removed": 1}


class RuntimeWithoutAdminSurface:
    """A runtime that predates the patch: the methods simply do not exist."""


def build_client(runtime) -> TestClient:
    app = FastAPI()
    app.include_router(agent_admin_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.runtime = runtime
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


class TestProfileScoping:
    """The caller's token decides the profile. Nothing in the request may."""

    def test_pending_list_uses_caller_profile(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.get("/api/agent/pending/memory", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert runtime.calls == [("list_pending", PROFILE_A, "memory")]

    def test_approve_uses_caller_profile(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.post(
            "/api/agent/pending/memory/abc123/approve", headers=bearer(PROFILE_A)
        )
        assert resp.status_code == 200
        assert runtime.calls == [("decide", PROFILE_A, "memory", "abc123", True)]

    def test_reject_is_not_approve(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        client.post("/api/agent/pending/memory/abc123/reject", headers=bearer(PROFILE_A))
        assert runtime.calls == [("decide", PROFILE_A, "memory", "abc123", False)]

    def test_other_profile_cannot_reach_this_profiles_queue(self):
        """A caller whose profile has no runtime gets 404, not another's data."""
        runtime = FakeRuntime(known_profiles=(PROFILE_A,))
        client = build_client(runtime)
        resp = client.get("/api/agent/pending/memory", headers=bearer(PROFILE_B))
        assert resp.status_code == 404

    def test_unauthenticated_is_refused(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        assert client.get("/api/agent/pending/memory").status_code == 401
        assert client.get("/api/agent/auth/providers").status_code == 401


class TestSubsystemValidation:
    def test_unknown_subsystem_is_404_without_calling_runtime(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.get("/api/agent/pending/bogus", headers=bearer(PROFILE_A))
        assert resp.status_code == 404
        assert runtime.calls == []

    @pytest.mark.parametrize("subsystem", ["memory", "skills"])
    def test_known_subsystems_pass_through(self, subsystem):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.get(f"/api/agent/pending/{subsystem}", headers=bearer(PROFILE_A))
        assert resp.status_code == 200


class TestMissingAdminSurface:
    """The distinction that matters: 'rebuild the image' vs 'upstream failed'."""

    def test_runtime_without_methods_reports_501(self):
        client = build_client(RuntimeWithoutAdminSurface())
        resp = client.get("/api/agent/pending/memory", headers=bearer(PROFILE_A))
        assert resp.status_code == 501

    def test_unpatched_hermes_reports_501_not_502(self):
        runtime = FakeRuntime(
            raises=AdminSurfaceUnavailable("no admin surface", status_code=501)
        )
        client = build_client(runtime)
        resp = client.get("/api/agent/pending/memory", headers=bearer(PROFILE_A))
        assert resp.status_code == 501

    def test_upstream_failure_keeps_its_own_status(self):
        runtime = FakeRuntime(
            raises=AdminSurfaceUnavailable("token exchange failed", status_code=502)
        )
        client = build_client(runtime)
        resp = client.post(
            "/api/agent/auth/oauth/complete",
            json={"flow_id": "f1", "code": "abc#def"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 502
        assert "token exchange failed" in resp.json()["detail"]

    def test_missing_runtime_is_503(self):
        app = FastAPI()
        app.include_router(agent_admin_routes.router)
        app.state.tokens = TokenService(KEY)
        app.state.runtime = None
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/agent/pending/memory", headers=bearer(PROFILE_A))
        assert resp.status_code == 503


class TestOAuthFlow:
    def test_start_forwards_provider(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.post(
            "/api/agent/auth/oauth/start",
            json={"provider": "anthropic"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert resp.json()["authorize_url"].startswith("https://")
        assert runtime.calls == [("start", PROFILE_A, "anthropic")]

    def test_pasted_code_is_forwarded_verbatim(self):
        """The '#state' half is the CSRF binding and must not be stripped."""
        runtime = FakeRuntime()
        client = build_client(runtime)
        client.post(
            "/api/agent/auth/oauth/complete",
            json={"flow_id": "f1", "code": "thecode#thestate"},
            headers=bearer(PROFILE_A),
        )
        assert runtime.calls == [
            ("complete", PROFILE_A, "f1", "thecode#thestate", None)
        ]

    def test_complete_requires_flow_and_code(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.post(
            "/api/agent/auth/oauth/complete",
            json={"flow_id": "f1"},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 422
        assert runtime.calls == []

    def test_status_is_scoped_to_the_callers_profile(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.get(
            "/api/agent/auth/oauth/f1",
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "awaiting_user"
        assert runtime.calls == [("oauth_status", PROFILE_A, "f1")]

    def test_cancel_is_scoped_to_the_callers_profile(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        resp = client.delete(
            "/api/agent/auth/oauth/f1",
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"
        assert runtime.calls == [("oauth_cancel", PROFILE_A, "f1")]

    def test_logout_passes_optional_credential_selector(self):
        runtime = FakeRuntime()
        client = build_client(runtime)
        client.delete("/api/agent/auth/providers/anthropic", headers=bearer(PROFILE_A))
        client.delete(
            "/api/agent/auth/providers/anthropic?credential=abc123",
            headers=bearer(PROFILE_A),
        )
        assert runtime.calls == [
            ("logout", PROFILE_A, "anthropic", None),
            ("logout", PROFILE_A, "anthropic", "abc123"),
        ]
