# Worked Examples: Finding Verification

Use these examples on demand when reproducing, narrowing, or refuting an existing finding
and validating the output with `scripts/validate_workflow.py`.

## 1. Candidate Finding Input and Isolated Reproduction Trace

Reported finding `F-014` claims `SessionCache.refresh()` loses the latest token when an
earlier refresh aborts synchronously during `AbortController.abort()`:

```javascript
// src/auth/session_cache.mjs (lines 14-31)
export class SessionCache {
  constructor() {
    this.generation = 0;
    this.currentAbort = null;
    this.token = null;
  }
  async refresh(fetcher) {
    if (this.currentAbort) this.currentAbort.abort(); // fires abort listener synchronously!
    const gen = ++this.generation;
    const ctrl = new AbortController();
    this.currentAbort = ctrl;
    ctrl.signal.addEventListener("abort", () => {
      if (this.generation === gen) this.token = null;
    });
    const next = await fetcher(ctrl.signal);
    if (this.generation === gen) this.token = next;
    return this.token;
  }
}
```

Checking both the failure hypothesis and safe control:
- Calling `this.currentAbort.abort()` *before* `++this.generation` means `this.generation`
  still equals the *previous* `gen` during synchronous abort dispatch, clearing `this.token`
  briefly, while a reentrant caller inside an external abort listener sees stale generation
  ordering. Sequential non-overlapping refreshes pass cleanly.

## 2. Valid Finding Verification Deliverable (`assets/finding-verification.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_workflow.py finding-verification.json`:

```json
{
  "schema_version": "1.0",
  "finding_id": "F-014",
  "revision": "9b41e20",
  "reproduction_status": "confirmed",
  "reproduced": true,
  "safe_controls_passed": true,
  "safe_control_outcomes": [
    {
      "control": "Sequential non-overlapping SessionCache.refresh calls preserve latest token",
      "passed": true,
      "details": "Two sequential refresh calls returned 'tok-2' with generation === 2."
    },
    {
      "control": "Out-of-order delayed resolution without abort listener reentrancy",
      "passed": true,
      "details": "Final generation === gen guard blocked stale 'tok-1' overwrite."
    }
  ],
  "confidence_rating": "high",
  "commands": [
    "node --input-type=module -e \"import {SessionCache} from './src/auth/session_cache.mjs'; ...\""
  ],
  "result": "Reproduced synchronous abort clearing committed token before generation increment when refresh B starts after refresh A.",
  "notes": "Increment this.generation before invoking previous currentAbort.abort() so abort callbacks observe superseded generation immediately."
}
```
