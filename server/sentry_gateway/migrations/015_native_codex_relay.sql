-- Native Codex relay over the existing outbound execution-node channel.
--
-- The Gateway remains the durable authority.  Codex credentials and local
-- paths stay on the owning node; only named workspace IDs, sanitized events,
-- and user decisions cross this boundary.

ALTER TABLE execution_nodes
    ADD COLUMN IF NOT EXISTS native_runtimes JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS runtime_session_id TEXT;

ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS runtime_model TEXT;

CREATE TABLE IF NOT EXISTS work_order_events (
    work_order_id  UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    event_index    INTEGER NOT NULL CHECK (event_index > 0),
    event_type     TEXT NOT NULL,
    summary        TEXT NOT NULL DEFAULT '',
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (work_order_id, event_index)
);

CREATE INDEX IF NOT EXISTS work_order_events_order_idx
    ON work_order_events(work_order_id, event_index);

CREATE TABLE IF NOT EXISTS work_order_responses (
    work_order_id  UUID NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    request_id     TEXT NOT NULL,
    response       JSONB NOT NULL,
    responded_by   UUID NOT NULL REFERENCES users(id),
    responded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (work_order_id, request_id)
);
