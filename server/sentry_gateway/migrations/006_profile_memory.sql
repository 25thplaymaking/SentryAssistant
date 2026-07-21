-- Per-profile durable memory, owned by the Gateway (not proxied from the agent,
-- whose API server exposes no memory endpoint). One writable store per profile;
-- never shared across profiles. Sections mirror the fork's memory surfaces
-- (e.g. notes / user / soul / project), but the section name is caller-defined.
CREATE TABLE IF NOT EXISTS profile_memory (
    profile_id  UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    section     TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_id, section)
);
