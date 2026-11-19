# Architectural Decision Records: Specialist Method and Examples

## Reconstructing Decision Drivers, Viable Alternatives, and Honest Costs

1. Trace concrete technical drivers across dependency manifests, database transaction scopes, and deployment topologies (`rg -n "transaction|outbox|retry|idempotency"`), distinguishing verified source constraints from unconfirmed roadmap aspirations.
2. Compare at least two realistic architectural options against identical failure modes—partial-write durability, tail-latency coupling, schema-evolution windows, and operational rollback cost—rejecting strawman alternatives.
3. Verify every `accepted` or `draft` ADR records explicit negative trade-offs and operational burdens alongside positive outcomes before running `validate_architecture.py`.

## Failure Shape vs. Safe Control: Synchronous Dual-Write vs. Transactional Outbox Decision

```python
# Fragile alternative rejected by ADR: dual-write across SQL commit and broker RPC
def place_order_dual_write(db_conn, broker_client, order_cmd):
    with db_conn.transaction():
        order_id = db_conn.insert_order(order_cmd)
    # Broker outage after commit drops OrderPlaced event permanently!
    broker_client.publish("orders.placed", {"order_id": order_id})
    return order_id


# Adopted ADR safe control: single ACID transaction persists order and outbox row
def place_order_with_outbox(db_conn, order_cmd):
    with db_conn.transaction():
        order_id = db_conn.insert_order(order_cmd)
        db_conn.insert_outbox_event(
            aggregate_id=order_id,
            event_type="orders.placed",
            dedup_key=f"order-placed:{order_id}",
        )
    return order_id
```

## Architecture Decision `architecture.json` Deliverable Example

Validate this ADR payload locally using `python3 <this-skill>/scripts/validate_architecture.py adr-output.json` (`assets/architecture.schema.json`):

```json
{
  "schema_version": "1.0",
  "title": "Order Event Dispatch Durability Decision Record",
  "status": "accepted",
  "boundaries": [
    {
      "name": "checkout-outbox-ledger",
      "type": "storage",
      "description": "Transactional Postgres schema in src/orders/store.py owning orders and outbox_events tables.",
      "invariants": [
        "Every committed order row writes its corresponding outbox_events record within the same database transaction.",
        "Relay workers mark published_at monotonically and never mutate order state columns."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-012",
      "title": "Adopt relational transactional outbox over synchronous broker publish in checkout",
      "context": "src/orders/checkout.py must commit customer orders within a 150ms p99 budget even when the downstream Kafka cluster experiences transient broker elections.",
      "decision": "Persist outbound domain events into outbox_events inside the primary Postgres transaction and relay asynchronously with idempotent consumer deduplication keys.",
      "consequences": [
        "Positive: Eliminates silent lost-event drift between committed orders and downstream fulfillment consumers.",
        "Negative: Introduces asynchronous relay lag (typically 100ms-2s) and requires vacuum/retention management on outbox_events.",
        "Operational: Downstream fulfillment handlers must enforce idempotency on dedup_key under at-least-once delivery."
      ]
    }
  ]
}
```
