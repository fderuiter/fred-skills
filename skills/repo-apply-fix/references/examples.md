# Worked Examples: Scoped Correction

Use these examples on demand when applying an authorized, minimal fix with its regression test
and validating the record via `scripts/validate_workflow.py`.

## 1. Authorized Minimal Patch Preserving Safe Controls

User authorized fixing `F-014` and `F-015` inside `src/auth/session_cache.mjs` and
`tests/auth/session_cache.test.mjs`:

```diff
--- a/src/auth/session_cache.mjs
+++ b/src/auth/session_cache.mjs
@@ -6,9 +6,10 @@ export class SessionCache {
   }
   async refresh(fetcher) {
-    if (this.currentAbort) this.currentAbort.abort();
     const gen = ++this.generation;
+    const prevAbort = this.currentAbort;
     const ctrl = new AbortController();
     this.currentAbort = ctrl;
+    if (prevAbort) prevAbort.abort();
     ctrl.signal.addEventListener("abort", () => {
       if (this.generation === gen) this.token = null;
     });
@@ -16,4 +17,9 @@ export class SessionCache {
     if (this.generation === gen) this.token = next;
     return this.token;
   }
+  logout() {
+    this.generation += 1;
+    if (this.currentAbort) this.currentAbort.abort();
+    this.currentAbort = null;
+    this.token = null;
+  }
 }
```

## 2. Valid Applied Correction Deliverable (`assets/fix.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py fix.json`:

```json
{
  "schema_version": "1.0",
  "finding_id": "F-014",
  "before_revision": "9b41e20",
  "after_revision": "9b41e20+dirty-session-cache",
  "file_changes": [
    {
      "path": "src/auth/session_cache.mjs",
      "action": "modified",
      "additions": 8,
      "deletions": 1
    },
    {
      "path": "tests/auth/session_cache.test.mjs",
      "action": "added",
      "additions": 46,
      "deletions": 0
    }
  ],
  "diff_summaries": [
    {
      "path": "src/auth/session_cache.mjs",
      "summary": "Advance generation and install new AbortController before aborting superseded controller; fence logout()."
    },
    {
      "path": "tests/auth/session_cache.test.mjs",
      "summary": "Add deferred-promise tests for synchronous abort reentrancy, post-logout settle, and sequential refresh safe control."
    }
  ],
  "execution_results": [
    {
      "command": "node --test tests/auth/session_cache.test.mjs",
      "exit_code": 0,
      "passed": true,
      "output": "tests 3 | pass 3 | fail 0"
    }
  ],
  "compatibility_limits": [
    "Abort listeners registered on superseded signals now observe the new controller already installed on SessionCache.currentAbort."
  ],
  "notes": "Ready for independent post-fix verification via repo-verify-fix."
}
```
