"""The scheduler loop's contracts, with the clock injected through tick(now=).

What must hold:
  - a matched minute fires exactly once, however many ticks see it;
  - a persisted claimed_minute inside the minute blocks a re-fire (restart guard);
  - a job still running is skipped, never stacked;
  - the fall-back repeated hour fires twice (the guard is a UTC instant, so
    PEP 495's fold-blind same-zone comparison cannot collapse the two 1:30s);
  - a completed turn stays ok even when the stream tail errors afterwards;
  - one failing job records last_status='failed' and never stops its neighbours;
  - a tick that hangs on the pool is abandoned, and the loop keeps ticking;
  - no pool means no fires (fail closed, same rule as chat).
"""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agent_runtime.base import RuntimeEvent, RuntimeEventType, RuntimeSession
from app.cron.scheduler import CronScheduler, run_job

PROFILE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

# Every-minute schedule keeps the clock arithmetic out of the tests.
T0 = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 21, 8, 1, tzinfo=timezone.utc)


def _job(schedule="* * * * *", prompt="do the thing"):
    return {
        "id": uuid4(), "profile_id": PROFILE,
        "schedule": schedule, "prompt": prompt,
        "enabled": True, "last_run_at": None, "claimed_minute": None,
    }


class FakePool:
    """Backs the scheduler's three queries against an in-memory job list,
    including the claimed_minute claim semantics the restart guard relies on."""

    def __init__(self, jobs):
        self.jobs = {j["id"]: j for j in jobs}
        self.status_writes = []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                class Conn:
                    async def fetch(self, q, *a):
                        return [dict(j) for j in pool.jobs.values() if j["enabled"]]

                    async def fetchval(self, q, *a):
                        if "SET claimed_minute = now()" in q and "RETURNING id" in q:
                            job = pool.jobs.get(a[0])
                            if job is None or not job["enabled"]:
                                return None
                            last = job["claimed_minute"]
                            if last is not None and last >= a[1]:
                                return None  # already claimed this minute
                            # Store the claimed minute, not the wall clock: the
                            # tests inject past minutes and now() would be
                            # ahead of every one of them.
                            job["claimed_minute"] = a[1]
                            return job["id"]
                        return None

                    async def execute(self, q, *a):
                        if "last_status" in q:
                            pool.status_writes.append(a)  # (id, status, summary)
                        return None

                return Conn()

            async def __aexit__(self, *_):
                return False

        return Ctx()


class FakeRuntime:
    def __init__(self, fail_profiles=(), gate: asyncio.Event | None = None):
        self.turns = []
        self._fail = set(fail_profiles)
        self._gate = gate  # when set, turns block until the test releases them

    async def create_session(self, profile_id, scope):
        return RuntimeSession(
            session_id=f"sess-{profile_id}", profile_id=profile_id,
            scope=SimpleNamespace(), created_at=datetime.now(timezone.utc),
        )

    async def send_turn(self, request):
        self.turns.append((request.profile_id, request.prompt))
        if request.profile_id in self._fail:
            raise RuntimeError("provider melted")
        if self._gate is not None:
            await self._gate.wait()
        yield RuntimeEvent(
            type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
            correlation_id=request.correlation_id,
            occurred_at=datetime.now(timezone.utc), summary="x" * 900,
        )


def scheduler_for(pool, runtime) -> CronScheduler:
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, runtime=runtime))
    return CronScheduler(app)


class TestFireOncePerMinute:
    async def test_matching_minute_fires_exactly_once(self):
        pool = FakePool([_job()])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)
        await s.tick(now=T0)  # the 30s poll sees each minute twice
        await s.drain()
        assert len(runtime.turns) == 1

    async def test_next_minute_fires_again(self):
        pool = FakePool([_job()])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)
        await s.drain()
        await s.tick(now=T1)
        await s.drain()
        assert len(runtime.turns) == 2

    async def test_non_matching_minute_does_not_fire(self):
        pool = FakePool([_job(schedule="30 8 * * *")])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)  # 08:00, schedule wants 08:30
        await s.drain()
        assert runtime.turns == []

    async def test_persisted_claimed_minute_blocks_a_restarted_gateway(self):
        # A restart forgets the in-memory minute; the column must still hold.
        job = _job()
        job["claimed_minute"] = T0.replace(second=10)
        pool = FakePool([job])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)  # fresh scheduler == fresh memory
        await s.tick(now=T0)
        await s.drain()
        assert runtime.turns == []

    async def test_a_manual_run_does_not_eat_the_next_scheduled_minute(self):
        # POST /{id}/run stamps last_run_at at completion but never touches
        # claimed_minute; the old single-column claim read that stamp as
        # "minute already fired" and silently skipped the scheduled fire.
        job = _job()
        job["last_run_at"] = T0.replace(second=10)  # manual run just finished
        pool = FakePool([job])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)
        await s.drain()
        assert len(runtime.turns) == 1


class TestFallBackRepeatedHour:
    async def test_both_folds_of_the_repeated_half_hour_fire(self):
        """America/New_York 2026-11-01: 1:30 EDT and 1:30 EST are distinct
        instants an hour apart, but PEP 495 makes them compare EQUAL in their
        own zone (fold is ignored intra-zone). A wall-time last-fired guard
        therefore swallowed the second 1:30; the guard must hold UTC instants."""
        ny = ZoneInfo("America/New_York")
        first = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)   # EDT, 05:30Z
        second = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=1)  # EST, 06:30Z
        # The trap this test pins: equal in-zone, an hour apart in UTC.
        assert first == second
        assert first.astimezone(timezone.utc) != second.astimezone(timezone.utc)

        pool = FakePool([_job(schedule="30 * * * *")])
        runtime = FakeRuntime()
        app = SimpleNamespace(state=SimpleNamespace(pool=pool, runtime=runtime))
        s = CronScheduler(app, zone=ny)
        await s.tick(now=first)
        await s.drain()
        await s.tick(now=second)
        await s.drain()
        assert len(runtime.turns) == 2


class TestRunningJobs:
    async def test_a_running_job_is_skipped_not_stacked(self):
        gate = asyncio.Event()
        pool = FakePool([_job()])
        runtime = FakeRuntime(gate=gate)
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)   # starts the job; it blocks on the gate
        await asyncio.sleep(0)
        await s.tick(now=T1)   # next minute arrives while it still runs
        gate.set()
        await s.drain()
        assert len(runtime.turns) == 1

    async def test_completion_summary_is_recorded_truncated(self):
        pool = FakePool([_job()])
        s = scheduler_for(pool, FakeRuntime())
        await s.tick(now=T0)
        await s.drain()
        assert pool.status_writes
        _, job_status, summary = pool.status_writes[-1]
        assert job_status == "ok"
        assert len(summary) == 500  # runtime yielded 900 chars

    async def test_message_deltas_beat_an_empty_completion_summary(self):
        """The /v1/responses path streams the answer as MESSAGE deltas and
        completes with an empty summary — observed live on the first fired
        job, which recorded ok with a blank last_summary."""
        class DeltaRuntime(FakeRuntime):
            async def send_turn(self, request):
                self.turns.append((request.profile_id, request.prompt))
                for text in ("OK", ""):
                    yield RuntimeEvent(
                        type=RuntimeEventType.MESSAGE, session_id=request.session_id,
                        correlation_id=request.correlation_id,
                        occurred_at=datetime.now(timezone.utc), summary=text,
                    )
                yield RuntimeEvent(
                    type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(timezone.utc), summary="",
                )
        pool = FakePool([_job()])
        s = scheduler_for(pool, DeltaRuntime())
        await s.tick(now=T0)
        await s.drain()
        _, job_status, summary = pool.status_writes[-1]
        assert job_status == "ok"
        assert summary == "OK"

    async def test_a_late_stream_error_cannot_flip_a_completed_turn(self):
        """The Hermes adapter synthesizes ERROR for any malformed trailing SSE
        line, so a stream can complete and then 'fail'. Completion is terminal:
        the turn ran, the reply exists, and the status must say so."""
        class TrailingErrorRuntime(FakeRuntime):
            async def send_turn(self, request):
                self.turns.append((request.profile_id, request.prompt))
                yield RuntimeEvent(
                    type=RuntimeEventType.MESSAGE, session_id=request.session_id,
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(timezone.utc), summary="all done",
                )
                yield RuntimeEvent(
                    type=RuntimeEventType.TURN_COMPLETED, session_id=request.session_id,
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(timezone.utc), summary="",
                )
                yield RuntimeEvent(
                    type=RuntimeEventType.ERROR, session_id=request.session_id,
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(timezone.utc),
                    summary="malformed trailing SSE line",
                )
        pool = FakePool([_job()])
        s = scheduler_for(pool, TrailingErrorRuntime())
        await s.tick(now=T0)
        await s.drain()
        _, job_status, summary = pool.status_writes[-1]
        assert job_status == "ok"
        assert summary == "all done"

    async def test_an_error_before_completion_still_fails(self):
        # Terminality must not weaken the honest case: no completion, no ok.
        class ErrorOnlyRuntime(FakeRuntime):
            async def send_turn(self, request):
                self.turns.append((request.profile_id, request.prompt))
                yield RuntimeEvent(
                    type=RuntimeEventType.ERROR, session_id=request.session_id,
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(timezone.utc), summary="provider 500",
                )
        pool = FakePool([_job()])
        s = scheduler_for(pool, ErrorOnlyRuntime())
        await s.tick(now=T0)
        await s.drain()
        _, job_status, summary = pool.status_writes[-1]
        assert job_status == "failed"
        assert summary == "provider 500"


class TestFailureIsolation:
    async def test_a_failing_job_records_failed_and_neighbours_still_fire(self):
        broken_profile = uuid4()
        broken = _job()
        broken["profile_id"] = broken_profile
        healthy = _job(prompt="still fires")
        pool = FakePool([broken, healthy])
        runtime = FakeRuntime(fail_profiles=(broken_profile,))
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)
        await s.drain()

        by_id = {a[0]: a for a in pool.status_writes}
        assert by_id[broken["id"]][1] == "failed"
        assert "provider melted" in by_id[broken["id"]][2]
        assert by_id[healthy["id"]][1] == "ok"

    async def test_a_bad_schedule_never_fires_and_never_raises(self):
        pool = FakePool([_job(schedule="not cron"), _job()])
        runtime = FakeRuntime()
        s = scheduler_for(pool, runtime)
        await s.tick(now=T0)
        await s.drain()
        assert len(runtime.turns) == 1  # only the valid job

    async def test_the_loop_survives_a_failing_tick(self):
        class ExplodingPool:
            def acquire(self):
                raise RuntimeError("db down")

        app = SimpleNamespace(
            state=SimpleNamespace(pool=ExplodingPool(), runtime=FakeRuntime())
        )
        s = CronScheduler(app, interval=0.01)
        s.start()
        await asyncio.sleep(0.05)  # several ticks, each raising inside
        assert not s._loop_task.done()  # the loop is still alive
        await s.stop()

    async def test_a_wedged_pool_cannot_freeze_the_loop(self, caplog):
        """A pool whose acquire never returns used to hang the tick — and the
        loop — forever, with no exception and no log line. The tick bound must
        abandon the hung attempt and keep ticking."""
        class WedgedPool:
            def __init__(self):
                self.acquires = 0

            def acquire(self):
                pool = self

                class Ctx:
                    async def __aenter__(self):
                        pool.acquires += 1
                        await asyncio.Event().wait()  # wedged: never returns

                    async def __aexit__(self, *_):
                        return False

                return Ctx()

        pool = WedgedPool()
        app = SimpleNamespace(state=SimpleNamespace(pool=pool, runtime=FakeRuntime()))
        s = CronScheduler(app, interval=0.01, tick_timeout=0.05)
        with caplog.at_level(logging.WARNING, logger="app.cron.scheduler"):
            s.start()
            await asyncio.sleep(0.3)
            assert not s._loop_task.done()   # still alive
            assert pool.acquires >= 2        # abandoned and retried, not frozen
            await s.stop()
        assert any("abandoned" in r.getMessage() for r in caplog.records)


class TestFailClosed:
    async def test_no_pool_fires_nothing(self):
        runtime = FakeRuntime()
        app = SimpleNamespace(state=SimpleNamespace(pool=None, runtime=runtime))
        await CronScheduler(app).tick(now=T0)
        assert runtime.turns == []

    async def test_audit_failure_refuses_the_run(self):
        # The audit INSERT raising must refuse the turn, mirroring chat.
        class AuditRefusingPool(FakePool):
            def acquire(self):
                pool = self

                class Ctx:
                    async def __aenter__(self):
                        class Conn:
                            async def execute(self, q, *a):
                                if "audit_events" in q:
                                    raise RuntimeError("audit table gone")
                                if "last_status" in q:
                                    pool.status_writes.append(a)
                                return None

                        return Conn()

                    async def __aexit__(self, *_):
                        return False

                return Ctx()

        job = _job()
        pool = AuditRefusingPool([job])
        runtime = FakeRuntime()
        job_status, _ = await run_job(
            pool, runtime, job_id=job["id"], profile_id=PROFILE, prompt="x"
        )
        assert job_status == "audit-unavailable"
        assert runtime.turns == []  # never ran unrecorded


class TestLifecycle:
    async def test_start_and_stop_are_clean(self):
        app = SimpleNamespace(state=SimpleNamespace(pool=None, runtime=None))
        s = CronScheduler(app, interval=0.01)
        s.start()
        await asyncio.sleep(0.03)
        await s.stop()
        assert s._loop_task is None
