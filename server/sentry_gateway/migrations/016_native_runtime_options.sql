-- User-selected native runtime controls travel with the durable work order.
-- The same compact JSON object is included in the signed dispatch and checked
-- again by the workstation node before Codex receives it.

ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS runtime_options JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Orders queued before this column existed must still carry an explicit,
-- signed control set.  The workstation rejects missing or mismatched native
-- metadata rather than guessing after dispatch.
UPDATE work_orders
SET runtime_options = jsonb_build_object(
    'action', 'turn',
    'collaboration_mode', 'default',
    'effort', NULL,
    'personality', 'pragmatic',
    'approval_policy', 'on-request',
    'sandbox', CASE WHEN mode = 'readOnly' THEN 'readOnly' ELSE 'workspaceWrite' END,
    'review_target', 'uncommittedChanges'
)
WHERE harness = 'codex' AND runtime_options = '{}'::jsonb;
