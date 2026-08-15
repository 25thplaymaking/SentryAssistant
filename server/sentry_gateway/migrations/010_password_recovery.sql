-- MFA-authorized password recovery codes minted by Server Control.
-- Plaintext leaves the Gateway once and is never stored.

CREATE TABLE IF NOT EXISTS password_recovery_codes (
    code_hash       TEXT PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    approved_by     TEXT NOT NULL CHECK (approved_by = 'server-control')
);

CREATE INDEX IF NOT EXISTS password_recovery_codes_user_active_idx
    ON password_recovery_codes(user_id, expires_at DESC)
    WHERE consumed_at IS NULL;
