-- Per-profile agent-runtime endpoints.
-- Hermes binds one process per profile (its own HERMES_HOME, port, and bearer
-- key), so the Gateway must resolve a distinct reachable endpoint per profile.
-- `profiles.runtime_home` records where a profile's data lives; this table
-- records how to reach the process that serves it.
--
-- The bearer key is stored as Fernet ciphertext. The key that decrypts it lives
-- only in the Gateway environment (SENTRY_RUNTIME_ENC_KEY), never in the
-- database, so a database dump alone never yields a usable Hermes credential.
CREATE TABLE IF NOT EXISTS runtime_endpoints (
    profile_id        UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    runtime_name      TEXT NOT NULL DEFAULT 'hermes',
    -- Full base URL including host and port, e.g. http://sentry-hermes-bryce:8642
    base_url          TEXT NOT NULL,
    -- Advertised to the runtime as the model id; identifies the profile's process.
    profile_name      TEXT NOT NULL,
    -- Fernet ciphertext of the bearer key. Never the plaintext key.
    api_key_encrypted TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runtime_endpoints_by_runtime
    ON runtime_endpoints(runtime_name);
