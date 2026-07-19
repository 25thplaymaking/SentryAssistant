-- Durable conversational work orders.
-- The rows here are the workflow and authorization authority. A runtime work
-- board is a rebuildable projection keyed to work_orders.id and can never move
-- an order on its own.

CREATE TABLE IF NOT EXISTS work_orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_by        UUID NOT NULL REFERENCES users(id),
    profile_id          UUID NOT NULL REFERENCES profiles(id),
    team_id             UUID REFERENCES teams(id),
    conversation_id     UUID NOT NULL,
    assigned_user_id    UUID REFERENCES users(id),
    execution_node_id   UUID REFERENCES execution_nodes(id),
    harness             TEXT,
    workspace_id        TEXT,
    title               TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN (
                            'draft','submitted','needsClarification','triaged',
                            'assigned','inProgress','needsInput','readyForReview',
                            'changesRequested','resolved','closed','cancelled','failed')),
    mode                TEXT NOT NULL CHECK (mode IN ('readOnly','workspaceWrite','approvedElevated')),
    completion_criteria TEXT[] NOT NULL DEFAULT '{}',
    correlation_id      TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    CONSTRAINT completion_criteria_present CHECK (
        state = 'draft' OR array_length(completion_criteria, 1) >= 1
    ),
    -- Assigned and InProgress cannot exist without full execution identity.
    CONSTRAINT execution_identity_present CHECK (
        state NOT IN ('assigned','inProgress')
        OR (assigned_user_id IS NOT NULL
            AND execution_node_id IS NOT NULL
            AND harness IS NOT NULL
            AND workspace_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS work_orders_team_state_idx ON work_orders(team_id, state);
CREATE INDEX IF NOT EXISTS work_orders_requester_idx ON work_orders(requested_by, created_at DESC);

-- Every transition is preserved. Nothing overwrites prior evidence.
CREATE TABLE IF NOT EXISTS work_order_transitions (
    id              BIGSERIAL PRIMARY KEY,
    work_order_id   UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    from_state      TEXT NOT NULL,
    to_state        TEXT NOT NULL,
    actor_user_id   UUID NOT NULL REFERENCES users(id),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason          TEXT,
    correlation_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS wo_transitions_order_idx
    ON work_order_transitions(work_order_id, occurred_at);

CREATE TABLE IF NOT EXISTS work_order_messages (
    id              BIGSERIAL PRIMARY KEY,
    work_order_id   UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    author_user_id  UUID REFERENCES users(id),
    author_kind     TEXT NOT NULL CHECK (author_kind IN ('user','assistant','system')),
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT NOT NULL,
    correlation_id  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS wo_messages_order_idx
    ON work_order_messages(work_order_id, created_at);

-- Each execution attempt is an immutable run. A changes-requested cycle adds a
-- new run rather than mutating the previous one.
CREATE TABLE IF NOT EXISTS work_order_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id   UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    attempt         INTEGER NOT NULL,
    harness         TEXT NOT NULL,
    execution_node_id UUID REFERENCES execution_nodes(id),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    outcome         TEXT CHECK (outcome IN ('succeeded','failed','cancelled')),
    -- Honest status boundary: implemented is not the same as deployed.
    status_boundary TEXT CHECK (status_boundary IN
                        ('implemented','tested','deployed','user-confirmed')),
    evidence_hash   TEXT,
    summary         TEXT,
    UNIQUE (work_order_id, attempt)
);

CREATE TABLE IF NOT EXISTS work_order_artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES work_order_runs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    uri             TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS work_order_subscriptions (
    work_order_id   UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason          TEXT NOT NULL CHECK (reason IN ('requester','assignee','watcher')),
    PRIMARY KEY (work_order_id, user_id, reason)
);

-- Agent-proposed skills stay inactive until an owner reviews the full diff.
CREATE TABLE IF NOT EXISTS skill_proposals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    team_id         UUID REFERENCES teams(id) ON DELETE CASCADE,
    proposed_by     UUID REFERENCES users(id),
    source_work_order UUID REFERENCES work_orders(id),
    name            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN (
                        'proposed','scanned','evaluated','awaitingApproval',
                        'rejected','active','superseded','rolledBack')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by     UUID REFERENCES users(id),
    reviewed_at     TIMESTAMPTZ
);

-- Only an explicitly approved content hash may ever be active in a profile.
CREATE UNIQUE INDEX IF NOT EXISTS skill_active_unique
    ON skill_proposals(profile_id, name) WHERE state = 'active';
