# Worked Examples: Regression Specification

Load these examples on demand when specifying deterministic regression tests with independent
oracles and validating the artifact with `scripts/validate_workflow.py`.

## 1. Deterministic Deferred-Promise Oracle vs. Flaky Sleep Anti-Pattern

For confirmed finding `F-014` (`SessionCache.refresh` synchronous abort clobbers active
token before incrementing generation), control completion order with explicit deferred
promises rather than `setTimeout`:

```javascript
// Deterministic regression harness (no sleeps, independent literal oracle)
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const cache = new SessionCache();
const first = deferred();
const second = deferred();

const p1 = cache.refresh(() => first.promise);
first.resolve("token-initial");
await p1; // cache.token === "token-initial"

let observedDuringAbort = null;
cache.currentAbort.signal.addEventListener("abort", () => {
  observedDuringAbort = cache.token;
});
const p2 = cache.refresh(() => second.promise);
// Before fix: abort ran while generation === 1, wiping cache.token to null!
second.resolve("token-renewed");
await p2;
```

## 2. Valid Regression Specification (`assets/regression.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py regression.json`:

```json
{
  "schema_version": "1.0",
  "finding_id": "F-014",
  "test_file": "tests/auth/session_cache.test.mjs",
  "failure_boundaries": [
    "SessionCache.refresh generation reservation vs synchronous AbortController.abort dispatch"
  ],
  "test_command_traces": [
    {
      "command": "node --test tests/auth/session_cache.test.mjs",
      "exit_code": 1,
      "output": "AssertionError: expected token during supersede abort to remain 'token-initial', got null"
    }
  ],
  "assertion_expectations": [
    {
      "description": "Superseding refresh reserves new generation before aborting prior controller",
      "expected": "cache.token remains 'token-initial' during synchronous abort and transitions to 'token-renewed' on resolve",
      "actual": "cache.token is cleared to null during synchronous abort because generation was still 1"
    },
    {
      "description": "Safe control: stale first promise resolving after second promise settles",
      "expected": "cache.token equals literal 'token-renewed'",
      "actual": "cache.token equals 'token-renewed'"
    }
  ],
  "notes": "Test written only in isolated scratch verification; authorize test edits before committing tests/auth/session_cache.test.mjs."
}
```
