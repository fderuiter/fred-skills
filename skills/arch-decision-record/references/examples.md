# Worked Examples: Architectural Decision Records

Load these examples on demand when evaluating architectural trade-offs and validating
decision records against `assets/architecture.schema.json` via `scripts/validate_architecture.py`.

## 1. Input Context: Dual-Write Webhook Failure vs. Transactional Outbox

Observed codebase problem in `src/orders/checkout_service.py`:

```python
def place_order(db_session, broker_client, cart):
    order = Order.from_cart(cart)
    db_session.add(order)
    db_session.commit()
    # Dual-write hazard: if broker publish times out after DB commit, downstream
    # fulfillment never receives OrderPlaced while customer card is charged.
    broker_client.publish("orders.placed", order.to_event())
    return order.id
```

Alternatives evaluated:
1. Two-phase distributed XA transactions across Postgres and RabbitMQ (rejected: operational
   lock contention and unsupported by broker driver).
2. Synchronous retry loop after `db_session.commit()` (rejected: process crash still drops
   committed events).
3. Transactional outbox table written in the same Postgres transaction with an idempotent
   relay worker (selected: preserves single-database ACID atomicity with explicit lag cost).

## 2. Valid ADR Deliverable (`assets/architecture.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_architecture.py architecture.json`:

```json
{
  "schema_version": "1.0",
  "title": "ADR-001: Adopt Postgres Transactional Outbox for Order Events",
  "status": "accepted",
  "boundaries": [
    {
      "name": "orders-outbox-storage",
      "type": "storage",
      "description": "Postgres orders and outbox_events tables committed in a single local transaction.",
      "invariants": [
        "Every committed orders row inserts a corresponding outbox_events row in the same transaction.",
        "Outbox relay marks published_at idempotently and downstream consumers deduplicate by event_id."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-001",
      "title": "Replace post-commit broker publish with transactional Postgres outbox",
      "context": "place_order in src/orders/checkout_service.py commits to Postgres and then publishes to RabbitMQ without atomicity, risking lost fulfillment events on broker timeout or worker crash.",
      "decision": "Write outbox_events rows inside the checkout_service Postgres transaction and poll/LISTEN from an asynchronous relay worker.",
      "consequences": [
        "Positive: Eliminates lost OrderPlaced events after database commit without distributed locks.",
        "Negative: Event delivery becomes at-least-once rather than at-most-once, requiring consumer deduplication on event_id.",
        "Operational: Requires monitoring outbox_events backlog age and periodic archival of published rows."
      ]
    }
  ]
}
```
