"""Route-level authentication behaviour.

These use the real dependency wiring with a stub database, so they prove the
routes fail closed without needing a live PostgreSQL.
"""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.tokens import Audience, TokenService
from app.routes import workorders as workorders_routes

KEY = "route-test-signing-key-padded-to-at-least-32-bytes"

USER = uuid4()
DEVICE = uuid4()
PROFILE = uuid4()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(workorders_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = None  # routes must reject before ever touching the database
    return TestClient(app, raise_server_exceptions=False)


def bearer(tokens: TokenService, audience: Audience = Audience.CLIENT) -> dict[str, str]:
    token = tokens.issue_access_token(
        user_id=str(USER), device_id=str(DEVICE), profile_id=str(PROFILE), audience=audience
    )
    return {"Authorization": f"Bearer {token}"}


class TestUnauthenticated:
    def test_missing_header_is_rejected(self, client):
        r = client.post("/api/workorders", json={"title": "t", "prompt": "p"})
        assert r.status_code == 401
        assert r.headers.get("www-authenticate") == "Bearer"

    def test_non_bearer_scheme_is_rejected(self, client):
        r = client.get(
            f"/api/workorders/{uuid4()}", headers={"Authorization": "Basic abc123"}
        )
        assert r.status_code == 401

    def test_garbage_token_is_rejected(self, client):
        r = client.get(
            f"/api/workorders/{uuid4()}",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401

    def test_token_from_another_key_is_rejected(self, client):
        foreign = TokenService("a-totally-different-key-padded-past-32-bytes")
        r = client.get(f"/api/workorders/{uuid4()}", headers=bearer(foreign))
        assert r.status_code == 401


class TestAudienceSeparation:
    def test_node_audience_token_cannot_call_client_routes(self, client):
        """A node credential must not act as a user."""
        tokens: TokenService = client.app.state.tokens
        r = client.get(
            f"/api/workorders/{uuid4()}", headers=bearer(tokens, Audience.NODE)
        )
        assert r.status_code == 401


class TestRevocation:
    def test_revoked_device_loses_access_immediately(self, client):
        tokens: TokenService = client.app.state.tokens
        headers = bearer(tokens)

        # Valid token gets past auth and fails at the database instead.
        assert client.get(f"/api/workorders/{uuid4()}", headers=headers).status_code == 503

        tokens.revoke_device(str(DEVICE))
        r = client.get(f"/api/workorders/{uuid4()}", headers=headers)
        assert r.status_code == 401
        assert "revoked" in r.json()["detail"].lower()


class TestFailClosedWithoutSigningKey:
    def test_routes_refuse_when_token_service_is_absent(self):
        app = FastAPI()
        app.include_router(workorders_routes.router)
        app.state.tokens = None  # no signing key configured
        app.state.pool = None
        c = TestClient(app, raise_server_exceptions=False)

        r = c.get(f"/api/workorders/{uuid4()}", headers={"Authorization": "Bearer x"})
        # 503, never 200: a misconfigured gateway must not serve anonymously.
        assert r.status_code == 503


class TestValidation:
    def test_empty_title_is_rejected(self, client):
        tokens: TokenService = client.app.state.tokens
        r = client.post(
            "/api/workorders", json={"title": "", "prompt": "p"}, headers=bearer(tokens)
        )
        assert r.status_code == 422

    def test_unknown_mode_is_rejected(self, client):
        tokens: TokenService = client.app.state.tokens
        r = client.post(
            "/api/workorders",
            json={"title": "t", "prompt": "p", "mode": "rootAccess"},
            headers=bearer(tokens),
        )
        assert r.status_code == 422

    def test_unknown_transition_target_is_rejected(self, client):
        tokens: TokenService = client.app.state.tokens
        r = client.post(
            f"/api/workorders/{uuid4()}/transition",
            json={"target": "wizard"},
            headers=bearer(tokens),
        )
        assert r.status_code == 422
