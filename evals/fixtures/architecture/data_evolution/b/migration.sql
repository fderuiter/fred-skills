-- Contract: rolling deployments keep N-1 and N application instances serving traffic concurrently.
-- Expand phase: additive nullable column while N-1 writers still populate customer_ref.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS account_ref TEXT;
-- Idempotent bounded backfill batch; NOT NULL and column drop deferred until N-1 is retired.
UPDATE orders SET account_ref = customer_ref WHERE account_ref IS NULL AND customer_ref IS NOT NULL;
