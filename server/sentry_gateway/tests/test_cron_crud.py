"""PUT/DELETE/run for /api/cron: profile-scoped, cross-profile access is a 404.

The fake pool is stateful: it holds one job owned by PROFILE_A and answers the
routes' SQL by inspecting the query text, so ownership predicates are exercised
rather than stubbed.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_runtime.base import RuntimeEvent, RuntimeEventType, RuntimeSession
from app.agent_runtime.hermes import UnknownProfileError
from app.auth.tokens import Audience, TokenService
from app.routes import cron as cron_routes

KEY = "cron-crud-test-signing-key-padded-well-past-the-32-byte-minimum"
PROFILE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PROFILE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _job(profile_id=PROFILE_A, prompt="morning brief"):
    return {
        "id": uuid4(), "profile_id": profile_id, "name": "brief",
        "schedule": "0 8 * * *", "prompt": prompt, "enabled": True,
        "created_at": datetime.now(timezone.utc),
        "last_run_at": None, "last_status": None, "last_summary": None,
    }


class FakePool:
    """One-table fake keyed on (id, profile_id), enough to prove scoping."""

    def __init__(self, jobs):
        self.jobs = {j["id"]: j for j in jobs}
        self.executed = []

    def _owned(self, job_id, profile_id):
        job = self.jobs.get(job_id)
        return job if job is not None and job["profile_id"] == profile_id else None

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetchrow(self, q, *a):
                        job = pool._owned(a[0], a[1])
                        if job is None:
                            return None
                        if "UPDATE profile_cron_jobs" in q:
                            for i, key in enumerate(
                                ("name", "schedule", "prompt", "enabled"), start=2
                            ):
                                if a[i] is not None:
                                    job[key] = a[i]
                        return dict(job)

                    async def fetchval(self, q, *a):
                        if "DELETE FROM profile_cron_jobs" in q:
                            job = pool._owned(a[0], a[1])
                            if job is None:
                                return None
                            del pool.jobs[job["id"]]
                            return job["id"]
                        return None

                    async def execute(self, q, *a):
                        pool.executed.append((q, a))
                        return None

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


class FakeRuntime:
    def __init__(self, known=(PROFILE_A,)):
        self._known = set(known)
        self.turns = []

    async def create_session(self, profile_id, scope):
        if profile_id not in self._known:
            raise UnknownProfileError(str(profile_id))
        return RuntimeSession(
            session_id=f"sess-{profile_id}", profile_id=profile_id,
            scope=scope, created_at=datetime.now(timezone.utc),
        )

    async def send_turn(self, request):
        self.turns.append((request.profile_id, request.prompt))
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=datetime.now(timezone.utc), summary="brief delivered",
        )


def build(pool, runtime=None) -> TestClient:
    app = FastAPI()
    app.include_router(cron_routes.router)
    app.state.tokens = TokenService(KEY)
    app.state.pool = pool
    app.state.runtime = runtime
    app.state.settings = object()
    return TestClient(app, raise_server_exceptions=False)


def bearer(profile_id: UUID) -> dict[str, str]:
    token = TokenService(KEY).issue_access_token(
        user_id=str(uuid4()), device_id=str(uuid4()),
        profile_id=str(profile_id), audience=Audience.CLIENT,
    )
    return {"Authorization": f"Bearer {token}"}


class TestUpdate:
    def test_partial_update_changes_only_sent_fields(self):
        job = _job()
        client = build(FakePool([job]))
        resp = client.put(
            f"/api/cron/{job['id']}",
            json={"schedule": "*/5 * * * *", "enabled": False},
            headers=bearer(PROFILE_A),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["schedule"] == "*/5 * * * *"
        assert body["enabled"] is False
        assert body["name"] == "brief"          # untouched
        assert body["prompt"] == "morning brief"  # untouched

    def test_bounds_match_create(self):
        job = _job()
        client = build(FakePool([job]))
        resp = client.put(
            f"/api/cron/{job['id']}", json={"name": ""}, headers=bearer(PROFILE_A)
        )
        assert resp.status_code == 422

    def test_another_profiles_job_is_404_not_403(self):
        job = _job(profile_id=PROFILE_A)
        resp = build(FakePool([job])).put(
            f"/api/cron/{job['id']}", json={"name": "mine now"},
            headers=bearer(PROFILE_B),
        )
        assert resp.status_code == 404

    def test_missing_job_is_404(self):
        resp = build(FakePool([])).put(
            f"/api/cron/{uuid4()}", json={"name": "x"}, headers=bearer(PROFILE_A)
        )
        assert resp.status_code == 404


class TestDelete:
    def test_own_job_is_deleted(self):
        job = _job()
        pool = FakePool([job])
        resp = build(pool).delete(f"/api/cron/{job['id']}", headers=bearer(PROFILE_A))
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        assert pool.jobs == {}

    def test_another_profiles_job_is_404_and_survives(self):
        job = _job(profile_id=PROFILE_A)
        pool = FakePool([job])
        resp = build(pool).delete(f"/api/cron/{job['id']}", headers=bearer(PROFILE_B))
        assert resp.status_code == 404
        assert job["id"] in pool.jobs  # nothing crossed the profile boundary


class TestRunNow:
    def test_run_now_fires_the_job_prompt(self):
        job = _job(prompt="post the standup summary")
        runtime = FakeRuntime()
        resp = build(FakePool([job]), runtime).post(
            f"/api/cron/{job['id']}/run", headers=bearer(PROFILE_A)
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "summary": "brief delivered"}
        assert runtime.turns == [(PROFILE_A, "post the standup summary")]

    def test_run_now_records_the_outcome(self):
        job = _job()
        pool = FakePool([job])
        build(pool, FakeRuntime()).post(
            f"/api/cron/{job['id']}/run", headers=bearer(PROFILE_A)
        )
        updates = [a for q, a in pool.executed if "last_status" in q]
        assert updates and updates[-1][1] == "ok"

    def test_another_profiles_job_is_404_and_never_runs(self):
        job = _job(profile_id=PROFILE_A)
        runtime = FakeRuntime(known=(PROFILE_A, PROFILE_B))
        resp = build(FakePool([job]), runtime).post(
            f"/api/cron/{job['id']}/run", headers=bearer(PROFILE_B)
        )
        assert resp.status_code == 404
        assert runtime.turns == []

    def test_unprovisioned_profile_reports_an_honest_failure(self):
        job = _job()
        runtime = FakeRuntime(known=())
        resp = build(FakePool([job]), runtime).post(
            f"/api/cron/{job['id']}/run", headers=bearer(PROFILE_A)
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert "runtime" in resp.json()["summary"]


def test_unauthenticated_is_refused():
    client = build(FakePool([]))
    job_id = uuid4()
    assert client.put(f"/api/cron/{job_id}", json={"name": "x"}).status_code == 401
    assert client.delete(f"/api/cron/{job_id}").status_code == 401
    assert client.post(f"/api/cron/{job_id}/run").status_code == 401
