-- Per-profile scheduled jobs (the Cron panel). Gateway-owned and authoritative;
-- actually firing a job is the Gateway scheduler's concern, kept separate. The
-- Hermes API exposes no cron endpoint, so this cannot be proxied from the agent.
CREATE TABLE IF NOT EXISTS profile_cron_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    schedule    TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS profile_cron_jobs_by_profile
    ON profile_cron_jobs(profile_id);
