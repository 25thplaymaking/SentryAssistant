-- Isolated personal profiles and shared team spaces.
-- A personal profile directory is never mounted into a team container, and a
-- team profile can never reach personal connectors, memory, or devices.

CREATE TABLE IF NOT EXISTS profiles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            TEXT NOT NULL CHECK (kind IN ('personal','team')),
    owner_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    display_name    TEXT NOT NULL,
    -- Each profile gets its own runtime home. Two profiles must never share one
    -- writable runtime data directory.
    runtime_home    TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ
);

-- Exactly one personal profile per user.
CREATE UNIQUE INDEX IF NOT EXISTS profiles_one_personal_per_user
    ON profiles(owner_user_id) WHERE kind = 'personal' AND archived_at IS NULL;

CREATE TABLE IF NOT EXISTS teams (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL UNIQUE REFERENCES profiles(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('owner','collaborator','observer')),
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Removal is a timestamp so past attributions remain readable.
    removed_at      TIMESTAMPTZ,
    PRIMARY KEY (team_id, user_id)
);

CREATE INDEX IF NOT EXISTS team_members_active_idx
    ON team_members(team_id) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS team_invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    invited_email   CITEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('owner','collaborator','observer')),
    invited_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    accepted_at     TIMESTAMPTZ,
    declined_at     TIMESTAMPTZ
);

-- Execution nodes are private by default. A node owner exposes only named
-- workspace capabilities to a team, never the rest of the machine.
CREATE TABLE IF NOT EXISTS execution_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id       UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS node_workspaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID NOT NULL REFERENCES execution_nodes(id) ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    -- The local path is resolved on the node itself; the Gateway stores only the
    -- identifier so a raw remote path can never be dispatched.
    allowed_harnesses TEXT[] NOT NULL DEFAULT '{}',
    allowed_modes   TEXT[] NOT NULL DEFAULT '{readOnly}',
    shared_with_team UUID REFERENCES teams(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (node_id, workspace_id)
);

-- Stored approval for a specific team/workspace/mode combination, so a repeat
-- run does not re-prompt. Elevated mode is deliberately not grantable here.
CREATE TABLE IF NOT EXISTS workspace_policy_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_workspace_id UUID NOT NULL REFERENCES node_workspaces(id) ON DELETE CASCADE,
    team_id         UUID REFERENCES teams(id) ON DELETE CASCADE,
    mode            TEXT NOT NULL CHECK (mode IN ('readOnly','workspaceWrite')),
    granted_by      UUID NOT NULL REFERENCES users(id),
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    UNIQUE (node_workspace_id, team_id, mode)
);
