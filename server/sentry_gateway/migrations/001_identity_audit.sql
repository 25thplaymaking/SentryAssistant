-- Sentry Gateway: identity, devices, and append-only audit.
-- Invite-only accounts. There is no public self-registration path.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id     TEXT UNIQUE,               -- OIDC subject once Authentik is wired
    display_name    TEXT NOT NULL,
    email           CITEXT,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('desktop','phone','browser','executionNode')),
    name            TEXT NOT NULL,
    enrolled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    -- Revocation is a timestamp rather than a delete so audit history survives.
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS devices_user_idx ON devices(user_id) WHERE revoked_at IS NULL;

-- Enrollment codes are stored only as hashes and expire in five minutes.
CREATE TABLE IF NOT EXISTS enrollment_codes (
    code_hash       TEXT PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_kind     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    approved_by     UUID REFERENCES users(id)
);

-- Refresh tokens rotate; only the hash is retained so a database read cannot
-- reconstruct a usable credential.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash      TEXT PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    rotated_to      TEXT,
    revoked_at      TIMESTAMPTZ
);

-- Single-use work-order nonces. A replayed order fails closed.
CREATE TABLE IF NOT EXISTS work_order_nonces (
    nonce           TEXT PRIMARY KEY,
    work_order_id   UUID NOT NULL,
    consumed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only audit. No UPDATE or DELETE grant is issued for this table.
CREATE TABLE IF NOT EXISTS audit_events (
    id              BIGSERIAL PRIMARY KEY,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id   UUID REFERENCES users(id),
    actor_device_id UUID REFERENCES devices(id),
    profile_id      UUID,
    team_id         UUID,
    action          TEXT NOT NULL,
    target_kind     TEXT,
    target_id       TEXT,
    decision        TEXT NOT NULL CHECK (decision IN ('allowed','denied','failed')),
    correlation_id  TEXT NOT NULL,
    -- Only a hash of evidence is stored here; the payload itself lives in
    -- profile-scoped object storage so audit rows never leak content.
    evidence_hash   TEXT,
    redacted_detail TEXT
);

CREATE INDEX IF NOT EXISTS audit_correlation_idx ON audit_events(correlation_id);
CREATE INDEX IF NOT EXISTS audit_actor_idx ON audit_events(actor_user_id, occurred_at DESC);

-- Enforce append-only at the database, not merely by convention in application code.
CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
