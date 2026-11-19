# Worked Examples: Audit Findings and Issue Planning

Consult these examples when consolidating multi-topic specialist findings, filtering refuted
candidates out of issue drafts, and ordering implementation dependencies.

## 1. Specialist Findings Input Requiring Consolidation and Filtering

Three specialist passes reported overlapping cache/cancellation symptoms plus one refuted
candidate:

```text
Candidate A (concurrency): Stale profile fetch overwrites newer tenant selection in ProfileStore.load()
Candidate B (cancellation): Navigating away during ProfileStore.load() aborts HTTP call, catch block resets state over newer load
Candidate C (trust-boundaries, status=refuted): Raw tenant_id query param suspected of IDOR, but auth middleware binds session tenant_id before controller runs
```

Consolidation decision:
- Merge Candidates A and B into one root-cause finding `F-010` (missing request-generation
  fence across both resolve and abort paths in `ProfileStore.load`) with tags
  `["cancellation", "state-machines"]` and one draft issue `ISS-001`.
- Retain Candidate C (`F-011`) in `findings` with `status: "refuted"` and `severity: "info"`,
  and **exclude** `F-011` from `issues` (`validate_audit.py` rejects issues referencing refuted
  findings).

## 2. Valid Consolidated `audit.json` Ledger Output

Validate the consolidated ledger with `python3 <this-skill>/scripts/validate_audit.py audit.json`
and optionally export SARIF via `python3 <this-skill>/scripts/audit_to_sarif.py audit.json -o audit.sarif`:

```json
{
  "schema_version": "1.0",
  "run_id": "run-2026-11-19-synthesis",
  "repository": {
    "identity": "acme/console-web",
    "revision": "c71e09a",
    "dirty": false,
    "snapshot": "git-tree:c71e09a"
  },
  "scope": [
    "src/stores/profile_store.ts",
    "src/middleware/tenant_auth.ts"
  ],
  "coverage": [
    {
      "topic": "synthesis",
      "scope": "src/stores/, src/middleware/",
      "status": "inspected",
      "evidence_ids": [
        "E-010",
        "E-011"
      ],
      "notes": "Consolidated concurrency and cancellation findings into F-010; kept refuted F-011 out of issues.",
      "next_action": "Hand off ISS-001 to repo-plan-fix or authorized implementation agent."
    }
  ],
  "contracts": [
    {
      "id": "C-010",
      "dimension": "concurrency-and-cancellation",
      "scope": "ProfileStore.load",
      "intended": "Only the most recently initiated tenant load may mutate activeProfile or loadingError.",
      "observed": "Both resolve and catch branches mutate store fields without checking request generation.",
      "enforcement": "unconditional assignment in src/stores/profile_store.ts",
      "evidence_ids": [
        "E-010"
      ]
    },
    {
      "id": "C-011",
      "dimension": "trust-boundaries",
      "scope": "requireTenantSession",
      "intended": "Authenticated session tenant overrides any query-string tenant hint.",
      "observed": "Middleware overwrites req.tenantId from signed session claims before route handlers execute.",
      "enforcement": "src/middleware/tenant_auth.ts",
      "evidence_ids": [
        "E-011"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-010",
      "path": "src/stores/profile_store.ts",
      "line_start": 28,
      "line_end": 54,
      "symbol": "ProfileStore.load",
      "context": "Assigns this.activeProfile on resolve and clears this.activeProfile = null in catch on abort."
    },
    {
      "id": "E-011",
      "path": "src/middleware/tenant_auth.ts",
      "line_start": 12,
      "line_end": 27,
      "symbol": "requireTenantSession",
      "context": "Rejects mismatched req.query.tenant_id with HTTP 403 before controller dispatch."
    }
  ],
  "findings": [
    {
      "id": "F-010",
      "topic": "concurrency",
      "tags": [
        "cancellation",
        "state-machines"
      ],
      "title": "Unfenced resolve and abort handlers in ProfileStore.load clobber newer tenant state",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-010"
      ],
      "evidence_ids": [
        "E-010"
      ],
      "preconditions": "User switches from Tenant A to Tenant B before Tenant A request settles.",
      "sequence": [
        "load('tenant-a') starts and aborts when load('tenant-b') starts.",
        "load('tenant-b') resolves from warm cache and sets activeProfile = Tenant B.",
        "Aborted load('tenant-a') enters catch block asynchronously and sets activeProfile = null."
      ],
      "expected": "activeProfile retains Tenant B and ignores settlement of superseded load('tenant-a').",
      "actual": "Superseded catch/resolve branch overwrites Tenant B state.",
      "impact": "Blank or wrong-tenant profile view after rapid tenant switch.",
      "root_cause": "Missing monotonic requestId check guarding both fulfillment and error/abort mutations.",
      "counterevidence": "Single flight without overlapping tenant switches settles deterministically.",
      "remedy": "Increment a monotonic requestId on each load() entry and guard every state write in try/catch/finally with id === this.latestRequestId.",
      "acceptance": [
        "Out-of-order resolve and delayed abort of superseded load() leave newer tenant state intact."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace across resolve and catch paths; vitest not executed in synthesis pass."
      },
      "related_findings": []
    },
    {
      "id": "F-011",
      "topic": "trust-boundaries",
      "tags": [],
      "title": "Suspected tenant_id query parameter override blocked by session middleware",
      "status": "refuted",
      "severity": "info",
      "confidence": "high",
      "contract_ids": [
        "C-011"
      ],
      "evidence_ids": [
        "E-011"
      ],
      "preconditions": "Authenticated caller supplies foreign ?tenant_id=other.",
      "sequence": [
        "Request enters requireTenantSession middleware before controller.",
        "Middleware compares query parameter to signed session claim and returns 403."
      ],
      "expected": "Foreign tenant parameter rejected before data query.",
      "actual": "Middleware returns 403 Forbidden.",
      "impact": "No cross-tenant exposure.",
      "root_cause": "Authoritative middleware check precedes controller handler.",
      "counterevidence": "Verified all router mounts include requireTenantSession (E-011).",
      "remedy": "None required; retain existing middleware integration test.",
      "acceptance": [
        "Foreign tenant_id query continues returning HTTP 403."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Refuted by direct route-table and middleware inspection."
      },
      "related_findings": []
    }
  ],
  "issues": [
    {
      "id": "ISS-001",
      "title": "Guard ProfileStore.load state updates with monotonic request generation ID",
      "finding_ids": [
        "F-010"
      ],
      "depends_on": [],
      "related_issues": [],
      "existing_url": null,
      "labels": [
        "bug",
        "concurrency"
      ],
      "publication": "draft"
    }
  ],
  "limitations": [
    "Remote GitHub issue tracker offline; duplicate check performed against local docs only."
  ],
  "checks": []
}
```
