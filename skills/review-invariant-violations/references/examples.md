# Worked Examples: Invariant Verification

Load these examples on demand when reviewing code diffs for broken state, concurrency, or
resource-lifecycle invariants and validating `review.json` with `scripts/validate_review.py`.

## 1. Diff Introducing Lock Leak and Partial State Update Across Error Path

```diff
--- a/src/workers/lease_pool.go
+++ b/src/workers/lease_pool.go
@@ -40,8 +40,12 @@ func (p *LeasePool) Acquire(workerID string) error {
     p.mu.Lock()
-    defer p.mu.Unlock()
     p.activeCount++
+    if err := p.auditLogger.RecordAcquire(workerID); err != nil {
+        return err
+    }
     p.owners[workerID] = time.Now()
+    p.mu.Unlock()
     return nil
 }
```

Invariant analysis:
- Removing `defer p.mu.Unlock()` while adding an early `return err` on `RecordAcquire` failure
  leaves `p.mu` permanently locked (deadlocking all subsequent `LeasePool` callers) AND leaves
  `p.activeCount` incremented without a matching `p.owners[workerID]` entry.

## 2. Valid Invariant Review Output (`assets/review.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_review.py review.json`:

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "Early error return in LeasePool.Acquire leaks p.mu mutex and corrupts the activeCount == len(owners) invariant.",
  "findings": [
    {
      "id": "REV-001",
      "title": "Early return on RecordAcquire error leaves p.mu locked and deadlocks pool",
      "severity": "critical",
      "file": "src/workers/lease_pool.go",
      "line": 44,
      "description": "Removing defer p.mu.Unlock() causes Acquire to return with p.mu held whenever auditLogger.RecordAcquire returns a non-nil error, permanently wedging all callers of LeasePool.",
      "recommendation": "Restore defer p.mu.Unlock() immediately after p.mu.Lock(), or invoke auditLogger.RecordAcquire outside the critical section after releasing p.mu."
    },
    {
      "id": "REV-002",
      "title": "Failed RecordAcquire increments activeCount without recording owner entry",
      "severity": "high",
      "file": "src/workers/lease_pool.go",
      "line": 42,
      "description": "p.activeCount++ executes before the fallible RecordAcquire call, violating the pool invariant p.activeCount == len(p.owners) and permanently exhausting pool capacity after transient audit errors.",
      "recommendation": "Mutate p.activeCount and p.owners[workerID] together only after fallible preconditions succeed, or roll back p.activeCount-- on error."
    }
  ]
}
```
