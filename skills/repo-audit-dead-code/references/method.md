# Dead Code and Obsolete Compatibility Paths: Specialist Method and Examples

## Separating Unreachable Hazard Paths From Live Dynamic Entry Points

1. Trace candidate unused symbols, retired feature flags, legacy v1 shims, and orphaned background consumers across static call graphs AND dynamic wiring (DI containers, CLI entrypoints, reflection registries, webhook routers, SQL migrations).
2. Prioritize dead or half-retired compatibility paths that still execute import-time side effects, hold open sockets/threads, weaken authz fallbacks, or mislead maintainers auditing security gates.
3. Never classify sequential database migration scripts, public SDK exports, or plugin hooks as dead code solely from zero internal Python/TS callers without checking deployment/package entrypoints.

## Failure Trace vs. Safe Control: Hardcoded-Off Flag Still Spawning Import-Time Consumer vs. Clean Removal

```python
# Defect trace: LEGACY_V1_BRIDGE_ENABLED is hardcoded False, yet importing bridge.py still opens Redis pub/sub thread!
LEGACY_V1_BRIDGE_ENABLED = False
_legacy_subscriber = start_daemon_pubsub_listener(
    "v1.events"
)  # runs unconditionally at module import!


def dispatch_event(event: dict[str, object]) -> None:
    if LEGACY_V1_BRIDGE_ENABLED:
        _legacy_subscriber.publish(event)
    publish_v2_event(event)


# Safe control: remove retired v1 shim after confirming zero external v1 producers, and never allocate sockets at import time
def dispatch_event_clean(event: dict[str, object]) -> None:
    publish_v2_event(event)
```

## Dead-Code `audit.json` Deliverable Example

Check this dead-code and obsolete-shim ledger locally with `python3 <this-skill>/scripts/validate_audit.py dead-code-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "dead-code-sample",
  "repository": {
    "identity": "acme/event-ingestor",
    "revision": "73ef95b",
    "dirty": false,
    "snapshot": "git-tree:73ef95b"
  },
  "scope": [
    "src/events/bridge.py"
  ],
  "coverage": [
    {
      "topic": "dead-code",
      "scope": "src/events/bridge.py",
      "status": "inspected",
      "evidence_ids": [
        "E-237"
      ],
      "notes": "Verified LEGACY_V1_BRIDGE_ENABLED constant, env overrides, and import graph across all entrypoints.",
      "next_action": "Hand off daemon thread shutdown review to repo-audit-lifecycle if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-237",
      "dimension": "dead-code",
      "scope": "Retired v1 pub/sub bridge in src/events/bridge.py",
      "intended": "Decommissioned v1 event bridge consumes zero Redis connections or background threads.",
      "observed": "LEGACY_V1_BRIDGE_ENABLED is hardcoded False with no env override, yet module import unconditionally spawns _legacy_subscriber.",
      "enforcement": "partial flag retirement leaving import-time resource allocation in src/events/bridge.py",
      "evidence_ids": [
        "E-237"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-237",
      "path": "src/events/bridge.py",
      "line_start": 8,
      "line_end": 16,
      "symbol": "_legacy_subscriber",
      "context": "Sets constant LEGACY_V1_BRIDGE_ENABLED = False on line 8, then calls start_daemon_pubsub_listener('v1.events') at module scope on line 9."
    }
  ],
  "findings": [
    {
      "id": "F-237",
      "topic": "dead-code",
      "tags": [
        "lifecycle",
        "resource-leak"
      ],
      "title": "Retired LEGACY_V1_BRIDGE path still opens Redis pub/sub connection and daemon thread on import",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-237"
      ],
      "evidence_ids": [
        "E-237"
      ],
      "preconditions": "Any API worker or CLI command imports src.events.bridge.",
      "sequence": [
        "Worker imports dispatch_event from src.events.bridge.",
        "Module top-level executes start_daemon_pubsub_listener('v1.events'), holding 1 Redis connection per worker.",
        "dispatch_event skips if LEGACY_V1_BRIDGE_ENABLED because constant is hardcoded False."
      ],
      "expected": "Retired v1 bridge allocates zero connections or threads and is removed once migration completes.",
      "actual": "Every worker pod leaks an idle Redis pub/sub socket and background thread for dead v1 code.",
      "impact": "200-worker deployment wastes 200 Redis client slots and fails boot if legacy Redis ACL is tightened.",
      "root_cause": "Feature flag constant was flipped to False without removing top-level listener initialization.",
      "counterevidence": "Historical migration scripts in migrations/ remain live for fresh test DB setups and were excluded.",
      "remedy": "Delete LEGACY_V1_BRIDGE_ENABLED, _legacy_subscriber, and dead publish branch after confirming v1 channel has 0 publishers.",
      "acceptance": [
        "Importing src.events.bridge opens zero network sockets and spawns zero threads."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Confirmed zero references or env overrides mutate LEGACY_V1_BRIDGE_ENABLED across repository."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
