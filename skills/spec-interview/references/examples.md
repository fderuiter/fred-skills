# Pre-Code Specification Interview Worked Examples

## Example 1: Grounding Discovery in Codebase Reality vs. Asking Trivial Questions

Before prompting the operator about a requested webhook replay endpoint, inspect existing
repository interfaces (`src/webhooks/store.py` and `tests/test_webhooks.py`):

```python
# Observed in src/webhooks/store.py — answers storage & signature questions directly
class WebhookDeliveryStore:
    def fetch_failed_since(
        self, tenant_id: str, cutoff_epoch: int, limit: int = 200
    ) -> list[DeliveryRecord]:
        """Return failed HMAC-verified deliveries ordered by (created_at, delivery_id)."""
        ...
```

- **Weak question (disallowed)**: "Which database stores webhook events, and how are signatures verified?" (Already answered by `src/webhooks/store.py`.)
- **Grounded Socratic batch**:
  1. `DEC-01` (`edge-case`): `fetch_failed_since` caps batches at 200 rows; should replay enqueue an asynchronous cursor job or reject windows exceeding 200 events synchronously with HTTP `422`?
  2. `DEC-02` (`tradeoff`): Should replayed deliveries reuse the original `Idempotency-Key` header or mint a replay-suffixed key so downstream receivers that expired their 24-hour dedupe cache still process the event?

## Example 2: Settled Pre-Code Interview Output (`spec.json`)

Once the operator resolves the bounded question batch, record the settled decisions, authorized
`new_file_scopes`, `out_of_scope` exclusions, and `verification_commands` in `spec.json` and
validate locally with `python3 <this-skill>/scripts/validate_spec.py spec.json`:

```json
{
  "schema_version": "1.0",
  "title": "Tenant-Scoped Failed Webhook Replay Endpoint",
  "status": "approved",
  "summary": "Pre-code interview decisions locking synchronous batch limits, idempotency semantics, and module boundaries before authoring SPEC.md.",
  "interview_decisions": [
    {
      "id": "DEC-01",
      "category": "edge-case",
      "question": "How should replay requests exceeding the 200-delivery store cap behave?",
      "decision": "Return HTTP 422 with next_cutoff_epoch hint rather than introducing background job state.",
      "rationale": "Keeps replay stateless and avoids adding a new queue worker dependency."
    },
    {
      "id": "DEC-02",
      "category": "tradeoff",
      "question": "Should replayed deliveries reuse the original Idempotency-Key or append a replay attempt counter?",
      "decision": "Preserve the original Idempotency-Key and add X-Webhook-Replay-Attempt metadata header.",
      "rationale": "Prevents duplicate side effects on receivers whose idempotency window is still active."
    },
    {
      "id": "DEC-03",
      "category": "scope",
      "question": "Should dead-letter retention TTL or webhook secret rotation be modified in this change?",
      "decision": "Keep retention TTL and secret rotation strictly out of scope.",
      "rationale": "Isolates the change to replay handler and delivery store read queries."
    }
  ],
  "new_file_scopes": [
    "src/webhooks/replay.py",
    "src/webhooks/routes.py",
    "tests/test_webhook_replay.py"
  ],
  "out_of_scope": [
    "Background worker queues or async cron scheduling",
    "Webhook HMAC secret rotation and retention schema migrations"
  ],
  "verification_commands": [
    "python -m unittest tests/test_webhook_replay.py -v"
  ]
}
```
