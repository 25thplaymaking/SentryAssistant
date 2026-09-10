-- Execution state for profile cron jobs. 008 gave the panel authoritative
-- definitions; nothing ever fired them. The Gateway scheduler now does, and
-- these columns are both its restart guard and the panel's answer to "did my
-- job actually run".
--
--   last_run_at   — claim marker. The scheduler fires a job only when this is
--                   older than the matched minute, so a restart mid-minute (or
--                   a second Gateway) cannot double-fire.
--   last_status   — 'ok', 'failed', or 'audit-unavailable' (refused rather
--                   than run unrecorded, same rule as chat turns).
--   last_summary  — bounded completion summary or failure reason. The full
--                   transcript lives in agent_actions, not here.

ALTER TABLE profile_cron_jobs ADD COLUMN IF NOT EXISTS last_run_at  TIMESTAMPTZ;
ALTER TABLE profile_cron_jobs ADD COLUMN IF NOT EXISTS last_status  TEXT;
ALTER TABLE profile_cron_jobs ADD COLUMN IF NOT EXISTS last_summary TEXT;
