"""The Gateway cron scheduler: the thing that actually fires profile_cron_jobs.

The cron routes own the authoritative definitions; this module owns execution.
A fired job is a chat turn without the HTTP layer, and it keeps the same
fail-closed rule: no audit row, no run. A job that cannot be recorded is
refused with last_status='audit-unavailable', never run silently.

Schedules are five-field cron (min hour dom mon dow) evaluated in the zone
named by SENTRY_CRON_TIMEZONE (IANA name, read once at startup, default UTC).
The default matters: this deployment's operator lives in America/New_York while
the host clock is UTC, so an unset zone silently shifts every job by the
offset. Set it in deploy/linux/.env.

Double-fire protection is layered because each layer covers a different hole:
  - in-memory last-fired minute: the 30s poll sees each minute twice. Kept as
    a UTC instant, not local wall time: during the fall-back repeated hour the
    two local 1:30s compare EQUAL under PEP 495 (fold is ignored when both
    operands share a zone), so a wall-time guard would swallow the second one;
  - the claimed_minute claim UPDATE (migration 014): a restart (or second
    Gateway) mid-minute loses the claim race instead of firing again. This is
    deliberately NOT last_run_at — that column is the user-facing "last run",
    written at completion by manual and scheduled fires alike, and a manual
    fire that stamped the claim column would silently eat the next scheduled
    minute;
  - the running-job guard (try_begin/end): a job still executing is skipped,
    not stacked. POST /{id}/run shares this guard, so a manual fire cannot
    overlap a scheduled one (or another manual one).

Known divergences from Vixie cron, documented rather than fixed:
  - Spring-forward: a job scheduled inside the skipped local hour (02:00-02:59
    on the DST-start day in most US zones) simply does not fire that day. The
    loop only matches minutes that exist on the wall clock; there is no
    Vixie-style catch-up run after the jump.
  - Stepped-star day fields: '*/2' in day-of-month counts as "restricted" for
    the dom/dow OR rule here, so '0 0 */2 * 1' fires on odd days OR Mondays.
    Vixie treats any field with a leading '*' as unrestricted, which would
    make the same schedule fire only on Mondays that fall on odd days.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from ..actions.recorder import ActionRecorder
from ..agent_runtime.base import RuntimeEventType, RuntimeTurn, SessionScope
from ..agent_runtime.hermes import UnknownProfileError
from ..audit.service import AuditEvent, AuditService, Decision

logger = logging.getLogger(__name__)

#: last_summary is for the Tasks panel to glance at; agent_actions holds the rest.
SUMMARY_LIMIT = 500

#: How often the loop wakes. Half a minute, so no matched minute is missed.
TICK_SECONDS = 30.0

#: Upper bound on one tick. A wedged pool (acquire or a query that never
#: returns) would otherwise freeze the loop forever with no log line — the
#: exact silent-death failure the loop's catch-all exists to prevent. Two
#: minutes is far beyond any healthy tick and short enough that the loop
#: resumes matching within a few missed minutes.
TICK_TIMEOUT_SECONDS = 120.0

_FIELD_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _expand(spec: str, lo: int, hi: int) -> set[int]:
    """One cron field to its value set. Raises ValueError on anything malformed.

    Supports "*", "*/n", plain numbers, ranges "a-b", steps on either
    ("a-b/n", and "a/n" meaning a through the field maximum), and comma lists
    of all of the above.
    """
    values: set[int] = set()
    for part in spec.split(","):
        stepped = "/" in part
        step = 1
        if stepped:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step < 1:
                raise ValueError(f"step must be positive, got {step}")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        elif stepped:
            # "a/n" is Vixie shorthand for "a-max/n".
            start, end = int(part), hi
        else:
            start = end = int(part)
        if not (lo <= start <= end <= hi):
            raise ValueError(f"{spec!r} is outside {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return values


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """A parsed five-field schedule. dom/dow keep None for "*" because standard
    cron gives the day fields OR-semantics only when BOTH are restricted, and
    the full value set cannot represent "unrestricted" for that rule."""

    minutes: frozenset[int]
    hours: frozenset[int]
    dom: frozenset[int] | None
    months: frozenset[int]
    dow: frozenset[int] | None  # normalized 0-6, Sunday = 0

    @classmethod
    def parse(cls, text: str) -> CronSchedule:
        fields = text.split()
        if len(fields) != 5:
            raise ValueError(f"expected 5 cron fields, got {len(fields)}")
        expanded = [
            _expand(field, lo, hi)
            for field, (lo, hi) in zip(fields, _FIELD_BOUNDS)
        ]
        # Cron accepts both 0 and 7 for Sunday; normalize to 0.
        dow = {v % 7 for v in expanded[4]}
        return cls(
            minutes=frozenset(expanded[0]),
            hours=frozenset(expanded[1]),
            dom=None if fields[2] == "*" else frozenset(expanded[2]),
            months=frozenset(expanded[3]),
            dow=None if fields[4] == "*" else frozenset(dow),
        )

    def matches(self, moment: datetime) -> bool:
        if (
            moment.minute not in self.minutes
            or moment.hour not in self.hours
            or moment.month not in self.months
        ):
            return False
        dom_ok = self.dom is None or moment.day in self.dom
        # Python weekday(): Monday=0. Cron: Sunday=0.
        dow_ok = self.dow is None or (moment.weekday() + 1) % 7 in self.dow
        if self.dom is not None and self.dow is not None:
            # Standard cron: both day fields restricted means EITHER may match.
            return dom_ok or dow_ok
        return dom_ok and dow_ok


def resolve_zone(name: str) -> ZoneInfo:
    """SENTRY_CRON_TIMEZONE to a zone. A bad name falls back to UTC loudly:
    crashing startup over a typo would take down every route, but shifting
    every job silently is the failure this env var exists to prevent."""
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        logger.warning("SENTRY_CRON_TIMEZONE %r is not a known zone; using UTC", name)
        return ZoneInfo("UTC")


async def _finish(pool, job_id: UUID, status: str, summary: str) -> tuple[str, str]:
    """Persist the outcome. Best-effort: the run already happened, and a status
    write that raised into the scheduler would kill the loop over telemetry."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE profile_cron_jobs SET last_run_at = now(),"
                " last_status = $2, last_summary = $3 WHERE id = $1",
                job_id, status, summary,
            )
    except Exception:
        logger.warning("cron job %s outcome not recorded", job_id, exc_info=True)
    return status, summary


async def run_job(
    pool, runtime, *, job_id: UUID, profile_id: UUID, prompt: str, actor=None
) -> tuple[str, str]:
    """Fire one job now and return (status, summary).

    This is the ONLY runner: POST /{id}/run and the scheduler loop both call
    it, so manual and scheduled fires cannot drift. It mirrors chat_turn's
    fail-closed order — audit before the turn, refuse when audit is
    unavailable — with `actor` carrying the caller for manual fires and None
    for scheduled ones (the audit row still names the profile and job).
    """
    if pool is None:
        # Nowhere to audit and nowhere to record the refusal.
        return "audit-unavailable", "No database; refusing to run an unrecorded job."
    if not prompt.strip():
        # Validation before audit, as in chat_turn: a refused run must not
        # leave an audit row claiming it ran.
        return await _finish(pool, job_id, "failed", "Job has no prompt.")

    correlation_id = uuid4().hex
    try:
        await AuditService(pool).record(
            AuditEvent(
                action="cron.turn",
                decision=Decision.ALLOWED,
                correlation_id=correlation_id,
                actor_user_id=getattr(actor, "user_id", None),
                actor_device_id=getattr(actor, "device_id", None),
                profile_id=profile_id,
                target_kind="cron_job",
                target_id=str(job_id),
            )
        )
    except Exception:
        return await _finish(
            pool, job_id, "audit-unavailable",
            "Could not record this run for audit; refused.",
        )

    try:
        session = await runtime.create_session(
            profile_id, SessionScope(profile_id=profile_id)
        )
    except UnknownProfileError:
        return await _finish(
            pool, job_id, "failed",
            "No agent runtime is provisioned for this profile.",
        )

    turn = RuntimeTurn(
        session_id=session.session_id,
        profile_id=profile_id,
        prompt=prompt,
        correlation_id=correlation_id,
    )
    recorder = ActionRecorder(pool, profile_id)
    # A stream that ends without a completion frame is a failure, not a quiet ok.
    status, summary = "failed", "The turn produced no completion event."
    # The agent's answer arrives as MESSAGE deltas; the completion frame's own
    # summary is usually empty on the /v1/responses path. Verified live: the
    # first fired job recorded status ok with a blank summary. Prefer the
    # accumulated reply text, keep the completion summary as the fallback.
    reply = ""
    try:
        async for event in runtime.send_turn(turn):
            await recorder.record(event)
            if event.type is RuntimeEventType.MESSAGE:
                if len(reply) < SUMMARY_LIMIT:
                    reply += event.summary or ""
            elif event.type is RuntimeEventType.TURN_COMPLETED:
                status = "ok"
                summary = (reply.strip() or event.summary)[:SUMMARY_LIMIT]
            elif event.type in (RuntimeEventType.TURN_FAILED, RuntimeEventType.ERROR):
                # Completion is terminal. The Hermes adapter synthesizes ERROR
                # for any malformed trailing SSE line, so a stream that already
                # completed must not be flipped to failed by its own tail.
                if status != "ok":
                    status = "failed"
                    summary = (event.summary or event.type.value)[:SUMMARY_LIMIT]
    except Exception as exc:
        # Same terminality rule: a stream that raises after the completion
        # frame already delivered the turn; keep the ok and its reply.
        if status != "ok":
            status, summary = "failed", f"{type(exc).__name__}: {exc}"[:SUMMARY_LIMIT]
    return await _finish(pool, job_id, status, summary)


class CronScheduler:
    """Polls enabled jobs and fires the ones whose schedule matches the minute.

    Reads pool and runtime from app.state at every tick rather than capturing
    them at start, because main.py may repair or drop either at runtime and the
    scheduler must follow. One job's failure never reaches another job or the
    loop: each fire runs in its own task and records its own outcome.
    """

    def __init__(
        self,
        app,
        zone: ZoneInfo | None = None,
        interval: float = TICK_SECONDS,
        tick_timeout: float = TICK_TIMEOUT_SECONDS,
    ):
        self._app = app
        self._zone = zone or timezone.utc
        self._interval = interval
        self._tick_timeout = tick_timeout
        self._loop_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._running: set[UUID] = set()
        #: Keyed by job, valued by the UTC instant of the last fired minute.
        #: UTC, not local wall time: PEP 495 makes the fall-back hour's two
        #: local 1:30s compare equal, and a wall-time guard would skip the
        #: second one.
        self._last_fired: dict[UUID, datetime] = {}

    def try_begin(self, job_id: UUID) -> bool:
        """Claim the right to run a job now; False means it is already running.

        This is the single in-process overlap guard: the scheduler's own fires
        and POST /{id}/run both pass through it, so a manual fire can never
        stack on a scheduled one (or on another manual one). Every successful
        try_begin must be paired with end() in a finally.
        """
        if job_id in self._running:
            return False
        self._running.add(job_id)
        return True

    def end(self, job_id: UUID) -> None:
        self._running.discard(job_id)

    def start(self) -> None:
        self._loop_task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        tasks = [t for t in (self._loop_task, *self._tasks) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._loop_task = None

    async def drain(self) -> None:
        """Wait for in-flight fires; used by tests and shutdown, never the loop."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                # Bounded: a wedged pool inside the tick (acquire or a query
                # that never returns) would otherwise freeze this loop forever
                # and silently — no exception, no log line, no fires. The
                # timeout cancels the hung tick so the next iteration gets a
                # fresh try. asyncio.timeout, NOT wait_for: on 3.11 wait_for
                # swallows a cancellation that lands while the inner future is
                # already done, which turned stop() into a permanent hang
                # whenever ticks were failing fast.
                async with asyncio.timeout(self._tick_timeout):
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                logger.warning(
                    "cron tick exceeded %.0fs and was abandoned; "
                    "the database may be wedged", self._tick_timeout,
                )
            except Exception:
                # The loop outlives any bad tick; a dead scheduler fires nothing
                # and looks exactly like the bug this module replaces.
                logger.warning("cron tick failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def tick(self, now: datetime | None = None) -> None:
        pool = getattr(self._app.state, "pool", None)
        runtime = getattr(self._app.state, "runtime", None)
        if pool is None or runtime is None:
            # No pool means no audit and no claim guard: fail closed, fire nothing.
            return

        if now is None:
            now = datetime.now(self._zone)
        minute = now.astimezone(self._zone).replace(second=0, microsecond=0)
        # Guards compare this instant, never the local wall time: astimezone
        # honours fold, so the fall-back hour's repeated 1:30 becomes two
        # distinct UTC minutes instead of one that fires once.
        minute_utc = minute.astimezone(timezone.utc)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, profile_id, schedule, prompt"
                " FROM profile_cron_jobs WHERE enabled"
            )

        for row in rows:
            job_id = row["id"]
            try:
                schedule = CronSchedule.parse(row["schedule"])
            except ValueError:
                # An unparseable schedule can never match; leave the row for
                # the owner to fix rather than spam status writes every tick.
                continue
            if not schedule.matches(minute):
                continue
            if job_id in self._running or self._last_fired.get(job_id) == minute_utc:
                continue
            # Claim the minute in the database so a restart (or a second
            # Gateway) mid-minute loses the race instead of double-firing.
            # The claim column is claimed_minute (migration 014), NOT
            # last_run_at: last_run_at is the user-facing outcome stamp that
            # manual runs also write, and claiming through it let a manual
            # fire silently eat the next scheduled minute.
            async with pool.acquire() as conn:
                claimed = await conn.fetchval(
                    "UPDATE profile_cron_jobs SET claimed_minute = now()"
                    " WHERE id = $1 AND enabled"
                    " AND (claimed_minute IS NULL OR claimed_minute < $2)"
                    " RETURNING id",
                    job_id, minute_utc,
                )
            self._last_fired[job_id] = minute_utc
            if claimed is None:
                continue
            if not self.try_begin(job_id):
                # A manual run began during the claim round-trip; skip, don't
                # stack — same rule as a still-running scheduled fire.
                continue
            task = asyncio.get_running_loop().create_task(
                self._fire(pool, runtime, row)
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _fire(self, pool, runtime, row) -> None:
        job_id = row["id"]
        try:
            await run_job(
                pool, runtime,
                job_id=job_id,
                profile_id=row["profile_id"],
                prompt=row["prompt"],
            )
        except Exception:
            # run_job records its own failures; this catches only the recorder
            # of last resort so one job can never take the set down.
            logger.warning("cron job %s crashed", job_id, exc_info=True)
        finally:
            self.end(job_id)
