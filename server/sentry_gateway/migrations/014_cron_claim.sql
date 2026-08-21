-- Split the scheduler's double-fire claim off last_run_at. 013 overloaded one
-- column with two jobs: the user-facing "when did this last run" stamp AND the
-- scheduler's minute-claim marker. The overload had two real costs:
--
--   * a manual POST /{id}/run wrote last_run_at at completion, and the
--     scheduler's claim predicate (last_run_at < matched minute) then treated
--     that minute as already fired — a manual run silently ate the next
--     scheduled fire;
--   * the claim wrote last_run_at at the START of a run, so a crash mid-run
--     left a fresh timestamp sitting next to a stale status.
--
--   claimed_minute — scheduler-only claim marker, stamped when the scheduler
--                    wins a minute (a restart or second Gateway mid-minute
--                    loses the race instead of double-firing). Manual runs
--                    never touch it.
--
-- last_run_at/last_status/last_summary stay the user-facing outcome, written
-- at completion by manual and scheduled runs alike.

ALTER TABLE profile_cron_jobs ADD COLUMN IF NOT EXISTS claimed_minute TIMESTAMPTZ;
