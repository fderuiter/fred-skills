# Flakiness Isolation Method

## 1. Classify the Nondeterminism Vector

Map every intermittent failure candidate to a concrete mechanism:

- **Async wait / wall-clock race**: `sleep()` polling, arbitrary timeouts, unawaited promises/goroutines/tasks, or callback ordering assumptions.
- **Order-dependent shared state**: module globals, singleton caches, environment variables, shared database rows, or uncleaned temp directories mutated across test cases.
- **Clock and calendar boundaries**: midnight UTC rollovers, DST transitions, monotonic vs wall-clock drift, or certificate/token TTL expiry close to assertion thresholds.
- **Resource contention**: hardcoded localhost ports, shared lockfiles, or fixed named queues colliding under parallel test workers.
- **Unseeded randomness or iteration order**: unseeded PRNGs, random UUIDs coupled to ordering assertions, or unordered map/set iteration serialized into snapshots.

## 2. Separate Harness Flake from Production Race

Before specifying a test change, trace whether production code violates a concurrency or ordering contract:

1. If production guarantees eventual completion only after an observable handle/event resolves and the test ignored that handle in favor of `sleep()`, classify as **test harness flakiness**.
2. If production promises synchronous or linearizable visibility and fails under concurrent scheduling, classify as a **production concurrency defect**—specify a deterministic interleaving reproducer rather than papering over the test.

## 3. Specify Deterministic Isolation Replacements

| Anti-pattern | Deterministic replacement |
| --- | --- |
| `time.sleep(0.1)` before assertion | Explicit completion future, condition variable, latch, or injected fake clock |
| Retry decorator (`@flaky(max_runs=3)`) | Root-cause synchronization barrier or isolated fixture state |
| Hardcoded port `8080` or `/tmp/test.db` | OS-assigned port (`127.0.0.1:0`) and per-test temporary directory |
| Module-level mutable cache | Per-test fixture instantiation or guaranteed `teardown`/`addCleanup` reset |
| Unseeded `random` / `Date.now()` | Seeded RNG logged on failure and injectable clock interface |

## 4. Verification Criteria

- Shuffle execution order (`--random-order` / `-shuffle=on`) to expose hidden state coupling between tests.
- Repeat targeted specs under parallel load (`-count=20`, `-n auto`) when suite execution is authorized, recording exact commands and outcomes in `testing.json` `execution_summary`.
