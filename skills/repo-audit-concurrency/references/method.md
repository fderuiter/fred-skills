# Concurrency and Race Conditions: Specialist Method and Examples

## Probing Interleavings Across Suspension Points and Shared Writers

1. Locate shared mutable fields, caches, and database rows written across `await` boundaries, worker threads, or actor reentrancy points (`rg -n "await |Lock|Mutex|atomic|Promise\\.all"`), mapping every reader and writer of the same state.
2. Construct explicit step-numbered schedules around suspension points where Operation B starts and finishes before Operation A resumes, checking stale-closure captures, check-then-act gaps, and lock-acquisition order cycles.
3. Verify whether monotonic generation counters, compare-and-swap predicates, or single-flight serializers guard *all* mutation branches (resolve, error, and finally) before filing.

## Failure Trace vs. Safe Control: Out-of-Order Search Autocomplete Overwrite

```typescript
// Vulnerable: slow query "py" resolves after fast query "python" and overwrites results
class SearchController {
  results: string[] = [];
  async onQueryChange(term: string) {
    const items = await this.api.search(term); // suspension point
    this.results = items; // stale write when earlier request resolves last!
  }
}

// Safe control: monotonic sequence fence guards every post-await state publication
class FencedSearchController {
  private seq = 0;
  results: string[] = [];
  async onQueryChange(term: string) {
    const ticket = ++this.seq;
    const items = await this.api.search(term);
    if (ticket !== this.seq) return;
    this.results = items;
  }
}
```

## Concurrency `audit.json` Deliverable Example

Validate this quota-reservation ledger locally using `python3 <this-skill>/scripts/validate_audit.py concurrency-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "concurrency-sample",
  "repository": {
    "identity": "acme/search-ui",
    "revision": "74fe910",
    "dirty": false,
    "snapshot": "git-tree:74fe910"
  },
  "scope": [
    "src/search/search_controller.ts"
  ],
  "coverage": [
    {
      "topic": "concurrency",
      "scope": "src/search/search_controller.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-401"
      ],
      "notes": "Inspected async query dispatch, error path, and store writes across await points.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-401",
      "dimension": "concurrency",
      "scope": "SearchController.onQueryChange",
      "intended": "Displayed results always correspond to the latest user query string.",
      "observed": "onQueryChange assigns this.results after await without checking whether a newer query started.",
      "enforcement": "none in src/search/search_controller.ts",
      "evidence_ids": [
        "E-401"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-401",
      "path": "src/search/search_controller.ts",
      "line_start": 11,
      "line_end": 18,
      "symbol": "SearchController.onQueryChange",
      "context": "Awaits api.search(term) then assigns this.results = items unconditionally."
    }
  ],
  "findings": [
    {
      "id": "F-401",
      "topic": "concurrency",
      "tags": [
        "cancellation"
      ],
      "title": "Slow earlier search response overwrites newer autocomplete results",
      "status": "confirmed",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-401"
      ],
      "evidence_ids": [
        "E-401"
      ],
      "preconditions": "User types 'py' followed by 'python' while initial broad query takes longer on backend.",
      "sequence": [
        "onQueryChange('py') starts Request A.",
        "onQueryChange('python') starts Request B.",
        "Request B resolves first and sets results = ['python-3.12'].",
        "Request A resolves second and overwrites results = ['pyyaml', 'pytest']."
      ],
      "expected": "Results remain ['python-3.12'] matching latest input 'python'.",
      "actual": "Stale Request A overwrites results with broad 'py' matches.",
      "impact": "UI displays mismatched search results for the active input box value.",
      "root_cause": "Missing monotonic request ticket check across await suspension boundary.",
      "counterevidence": "Debouncing reduces frequency but does not prevent in-flight network reordering.",
      "remedy": "Track a monotonic request sequence counter and discard completions where ticket !== this.seq.",
      "acceptance": [
        "Deferred-promise harness resolving Request B before Request A retains Request B results."
      ],
      "validation": {
        "state": "executed",
        "commands": [
          "npx vitest run tests/search_controller.spec.ts"
        ],
        "result": "Confirmed failure under reversed deferred completion order."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
