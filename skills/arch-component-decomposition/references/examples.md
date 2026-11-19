# Worked Examples: Component Decomposition

Load these examples on demand when decomposing coupled modules into cohesive components and
validating `architecture.json` via `scripts/validate_architecture.py`.

## 1. Coupled Monolithic Module Input with Circular Imports

Import-graph inspection of `src/notifications/` shows cyclic runtime coupling between template
rendering, user preference lookup, and SMTP retry transport:

```text
Observed runtime cycle:
  src/notifications/dispatcher.py -> imports src/notifications/templates.py
  src/notifications/templates.py  -> imports src/users/preferences.py
  src/users/preferences.py        -> imports src/notifications/dispatcher.py (to send opt-out confirmation!)
```

Safe control check: Keep all three components in-process (`module` boundaries) rather than
splitting into separate network microservices, breaking the runtime cycle by emitting a typed
domain return event from `preferences.update_opt_out()` handled by an application orchestrator.

## 2. Valid Component Decomposition Specification (`assets/architecture.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_architecture.py architecture.json`:

```json
{
  "schema_version": "1.0",
  "title": "Notification and User Preference Module Decomposition",
  "status": "draft",
  "boundaries": [
    {
      "name": "user-preferences-module",
      "type": "module",
      "description": "Owns notification channel consent state and quiet-hours rules in src/users/preferences.py.",
      "invariants": [
        "Zero runtime imports from src/notifications/; returns PreferenceChangeResult value objects instead of invoking transport."
      ]
    },
    {
      "name": "notification-renderer-module",
      "type": "module",
      "description": "Pure deterministic template renderer accepting pre-fetched locale and user context.",
      "invariants": [
        "Performs no database or network I/O during template rendering."
      ]
    },
    {
      "name": "notification-delivery-adapter",
      "type": "service",
      "description": "Outbound SMTP and webhook transport worker executing retries and rate limits.",
      "invariants": [
        "Accepts rendered NotificationEnvelope payloads keyed by idempotency_key."
      ]
    }
  ],
  "decisions": [
    {
      "id": "ADR-007",
      "title": "Break preferences-to-dispatcher import cycle via application-layer orchestration",
      "context": "src/users/preferences.py imports src/notifications/dispatcher.py directly to send opt-out receipts, creating an initialization cycle and forcing SMTP mocks into unit tests.",
      "decision": "Keep pure consent state in user-preferences-module, pure rendering in notification-renderer-module, and coordinate opt-out confirmation in src/app/update_preferences_use_case.py.",
      "consequences": [
        "Positive: Restores acyclic module dependency graph and enables pure unit tests for template rendering and consent rules.",
        "Negative: Callers mutating preferences must invoke the application use case rather than calling repository methods directly."
      ]
    }
  ]
}
```
