# Test Suite Verification: Specialist Method and Examples

## Probing Order Independence, State Leakage, and Fault Sensitivity

1. Execute target test suites across forward, reversed, and repeated invocation orders using the repository's native runner (`python3 -m unittest`, `pytest`, `npm test`), capturing exact exit codes and wall-clock durations.
2. Inspect module-level caches, singleton registries, environment mutations, and shared filesystem paths (`rg -n "_CACHE|os\\.environ|GLOBAL| tempfile"`) whenever test outcomes shift under reordering or repetition.
3. Audit assertion sensitivity against guarded invariants—checking for swallowed async rejections, over-broad `except Exception` blocks, or leaked state—without leaving temporary mutations in the working tree.

## Failure Shape vs. Safe Control: Polluted Module Cache vs. Per-Test Isolated Instance

```python
# Order-dependent suite hazard in src/telemetry/event_buffer.py: module list leaks across tests
_SHARED_EVENTS: list[str] = []


def record_event(name: str) -> int:
    _SHARED_EVENTS.append(name)
    return len(_SHARED_EVENTS)
    # Test B passes only when Test A runs first and pre-populates _SHARED_EVENTS!


# Order-independent safe control: explicit instance buffer constructed fresh per test case
class EventBuffer:
    def __init__(self) -> None:
        self._events: list[str] = []

    def record_event(self, name: str) -> int:
        self._events.append(name)
        return len(self._events)
```

## Suite Verification `testing.json` Deliverable Example

Validate this suite-verification artifact locally using `python3 <this-skill>/scripts/validate_testing.py suite-verification.json` (`assets/testing.schema.json`):

```json
{
  "schema_version": "1.0",
  "component": "src/telemetry/event_buffer.py",
  "framework": "unittest",
  "test_cases": [
    {
      "id": "VERIFY-001",
      "name": "test_record_event_isolated_under_reverse_test_order",
      "description": "src/telemetry/event_buffer.py: module-global _SHARED_EVENTS retains prior test entries, causing reverse-order execution of tests/test_event_buffer.py to fail count assertions.",
      "oracle_type": "state",
      "verified": false
    }
  ]
}
```
