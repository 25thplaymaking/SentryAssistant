-- Native image inputs exist only until an outbound workstation claims them.
-- The authenticated Gateway validates each data URL before enqueueing it;
-- claim_work returns the images once and clears this column transactionally.

ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS input_images JSONB NOT NULL DEFAULT '[]'::jsonb;
