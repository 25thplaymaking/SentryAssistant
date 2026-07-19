-- One execution node per enrolled device.
--
-- Registration happens on every node start, so it must be idempotent. Without a
-- unique key on device_id each restart inserted a fresh node row, orphaning the
-- previous one and changing the node's identity underneath its own config --
-- after which every dispatch was refused as "targets a different execution node".

-- Collapse any duplicates created before this constraint existed, keeping the
-- oldest row per device so existing work orders keep pointing at a live node.
WITH ranked AS (
    SELECT id, device_id,
           row_number() OVER (PARTITION BY device_id ORDER BY created_at, id) AS position
    FROM execution_nodes
),
duplicates AS (
    SELECT id FROM ranked WHERE position > 1
)
DELETE FROM execution_nodes
WHERE id IN (SELECT id FROM duplicates)
  AND id NOT IN (SELECT execution_node_id FROM work_orders WHERE execution_node_id IS NOT NULL);

CREATE UNIQUE INDEX IF NOT EXISTS execution_nodes_device_unique
    ON execution_nodes(device_id);
