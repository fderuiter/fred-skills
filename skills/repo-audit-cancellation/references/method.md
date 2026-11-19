# Cancellation Propagation: Specialist Method and Examples

## Following Abort Signals Across Child Work and State Commits

1. Follow `AbortSignal`, `context.Context`, `CancellationToken`, or task-cancel handles from request entry points into every spawned HTTP fetch, subprocess, timer, and worker promise.
2. Spot faux-cancellation patterns such as `Promise.race([work(), onAbort])` or `asyncio.wait_for` shields where the caller returns early on abort while the losing child coroutine keeps running and mutates shared state upon completion.
3. Account for documented non-cancelable commit regions (such as finalizing an already-charged payment ledger entry) where post-commit state reconciliation is intentional rather than a leak.

## Failure Trace vs. Safe Control: Detached `Promise.race` Winner vs. Signal-Aware Fetch

```typescript
// Defect trace: Promise.race rejects on abort, yet fetchPreview keeps running and overwrites previewState
async function loadDocumentPreview(docId: string, signal: AbortSignal) {
  const abortPromise = new Promise<never>((_, reject) =>
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true })
  );
  const html = await Promise.race([
    fetch(`/api/docs/${docId}/preview`).then((r) => r.text()).then((body) => {
      previewStore.setHtml(docId, body); // runs even after signal aborted!
      return body;
    }),
    abortPromise,
  ]);
  return html;
}

// Safe control: forward signal into fetch() and fence store mutation on signal.aborted
async function loadDocumentPreviewSafe(docId: string, signal: AbortSignal) {
  const response = await fetch(`/api/docs/${docId}/preview`, { signal });
  const body = await response.text();
  if (signal.aborted) throw signal.reason ?? new DOMException("Aborted", "AbortError");
  previewStore.setHtml(docId, body);
  return body;
}
```

## Cancellation `audit.json` Output Example

Run `python3 <this-skill>/scripts/validate_audit.py cancellation-audit.json` to verify conformance with `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "cancellation-sample",
  "repository": {
    "identity": "acme/docs-web",
    "revision": "34de06a",
    "dirty": false,
    "snapshot": "git-tree:34de06a"
  },
  "scope": [
    "src/preview/loader.ts"
  ],
  "coverage": [
    {
      "topic": "cancellation",
      "scope": "src/preview/loader.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-214"
      ],
      "notes": "Traced AbortController propagation from route navigation handler through loadDocumentPreview.",
      "next_action": "Hand off timer cleanup review to repo-audit-lifecycle if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-214",
      "dimension": "cancellation",
      "scope": "loadDocumentPreview",
      "intended": "Aborting a preview load terminates network I/O and prevents canceled loads from mutating previewStore.",
      "observed": "Promise.race abandons awaiting the fetch without passing signal to fetch() or gating previewStore.setHtml.",
      "enforcement": "none on child fetch or store write in src/preview/loader.ts",
      "evidence_ids": [
        "E-214"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-214",
      "path": "src/preview/loader.ts",
      "line_start": 9,
      "line_end": 21,
      "symbol": "loadDocumentPreview",
      "context": "Wraps fetch() in Promise.race against abortPromise without forwarding { signal } or checking signal.aborted before previewStore.setHtml."
    }
  ],
  "findings": [
    {
      "id": "F-214",
      "topic": "cancellation",
      "tags": [
        "concurrency",
        "lifecycle"
      ],
      "title": "Aborted preview load continues background fetch and mutates previewStore after navigation",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-214"
      ],
      "evidence_ids": [
        "E-214"
      ],
      "preconditions": "User navigates from large doc-A to doc-B, triggering AbortController.abort() on doc-A.",
      "sequence": [
        "loadDocumentPreview('doc-A', signalA) starts fetch('/api/docs/doc-A/preview') without signalA.",
        "Navigation aborts signalA; Promise.race rejects caller immediately while doc-A fetch stays in flight.",
        "doc-A fetch resolves after doc-B rendered and calls previewStore.setHtml('doc-A', body)."
      ],
      "expected": "Aborting signalA cancels the underlying HTTP request and suppresses previewStore writes.",
      "actual": "Background fetch consumes bandwidth and writes stale preview state after cancellation.",
      "impact": "Canceled document loads waste client sockets and overwrite active UI state.",
      "root_cause": "Caller-side Promise.race detaches observation without propagating AbortSignal to child I/O.",
      "counterevidence": "Non-aborted single-document navigation completes normally.",
      "remedy": "Pass { signal } to fetch() and check signal.aborted before mutating previewStore.",
      "acceptance": [
        "Aborting signalA mid-flight cancels fetch and performs zero previewStore.setHtml calls."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace verified across navigation controller and loadDocumentPreview."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
