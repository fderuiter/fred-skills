-- Contract: rolling deployments keep N-1 and N application instances serving traffic concurrently.
ALTER TABLE orders RENAME COLUMN customer_ref TO account_ref;
ALTER TABLE orders ALTER COLUMN account_ref SET NOT NULL;
