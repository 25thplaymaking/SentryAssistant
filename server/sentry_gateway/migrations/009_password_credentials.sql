-- Sentry Gateway: username + password credentials.
--
-- WHY A SEPARATE TABLE AND A SEPARATE USERNAME
--
-- Onboarding a teammate is currently "wait while I mint you a five-minute
-- enrollment code". That is the right primitive for pairing a device and it is
-- kept, but it is the wrong primitive for handing someone a URL and letting
-- them sign in. This table is the second primitive.
--
-- The login name is NOT users.display_name. display_name is a presentation
-- string ("Alice", later "Alice Smith"), it carries no UNIQUE constraint, and
-- provision_teammate.py already looks users up by it -- so two teammates with
-- the same display name are legal today and would make a login lookup
-- ambiguous, and an operator tidying a name would silently change how someone
-- signs in. A credential needs a stable, unique, case-insensitive subject
-- identifier of its own.
--
-- It lives here rather than on `users` because a username exists only because
-- password sign-in exists: an account that authenticates by OIDC subject or by
-- enrollment code alone has no use for one, and a nullable column on the
-- identity core would be a column that is meaningful only half the time.
-- Dropping the credential row drops the login name with it, which is correct.
--
-- password_hash is a PHC string ($argon2id$v=19$m=...,t=...,p=...$salt$digest):
-- algorithm, cost parameters and per-hash salt all travel with the hash. That
-- is what lets a future cost increase apply to new hashes without invalidating
-- a single existing one -- the only way a cost increase ever actually happens.
-- No separate salt/params columns, because splitting them invites a row where
-- the params and the digest disagree.
--
-- failed_attempts + locked_until implement lockout in the database rather than
-- in process memory, so it survives a gateway restart and cannot be reset by
-- bouncing the container (see app/auth/passwords.py for the numbers: five
-- failures, fifteen minutes).

CREATE TABLE IF NOT EXISTS user_passwords (
    user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    -- CITEXT: "Alice" and "alice" are the same person at the login box.
    username        CITEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    -- Set when an operator provisions or resets the password. The login
    -- response reports it so the client can force a change before the user
    -- settles into a session on a credential the operator has seen.
    must_change     BOOLEAN NOT NULL DEFAULT TRUE,
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until    TIMESTAMPTZ,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Keep usernames typeable and unambiguous: no spaces, no leading
    -- punctuation, nothing that has to be quoted or percent-encoded.
    CONSTRAINT user_passwords_username_shape
        CHECK (length(username::text) BETWEEN 3 AND 32
               AND username::text ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$')
);

-- Lookup at the login box is by username, and the PRIMARY KEY is user_id, so
-- the UNIQUE constraint above is doing the index work. No extra index needed.

COMMENT ON TABLE user_passwords IS
    'Password sign-in credentials. One row per user who has a password; absence means password sign-in is not available for that account.';
