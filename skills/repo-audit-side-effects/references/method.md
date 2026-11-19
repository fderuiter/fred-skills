# Hidden Side Effects: Specialist Method and Examples

## Auditing Query/Selector Purity and Implicit State Mutation

1. Inspect read-sounding APIs (`get*`, `list*`, `preview*`, `format*`, `@property` getters, React render selectors, HTTP `GET` handlers) for direct or transitive writes to persistent storage, shared singletons, cursors, or session state.
2. Check whether calling an ostensibly observational method twice—or calling it from a dashboard/debug inspector before a downstream workflow—changes the result of subsequent operations.
3. Separate observationally pure lazy initialization or bounded LRU memoization (where callers observe identical domain outputs on repeated reads) from state-advancing side effects hidden inside getters.

## Failure Trace vs. Safe Control: Mutating `exported` Flag Inside Summary Preview Getter

```python
# Defect trace: previewing pending payout summary flips row.exported = True, causing actual CSV export to emit 0 rows
class PayoutBatch:
    def get_pending_summary_buggy(self) -> dict[str, int]:
        pending = [row for row in self.rows if not row.exported]
        for row in pending:
            row.exported = True  # hidden mutation inside read-only summary helper!
        return {
            "count": len(pending),
            "total_cents": sum(r.amount_cents for r in pending),
        }


# Safe control: keep summary projection pure and advance exported state only in explicit commit_export() command
class PurePayoutBatch:
    def get_pending_summary(self) -> dict[str, int]:
        pending = [row for row in self.rows if not row.exported]
        return {
            "count": len(pending),
            "total_cents": sum(r.amount_cents for r in pending),
        }

    def mark_exported(self) -> list[PayoutRow]:
        pending = [row for row in self.rows if not row.exported]
        for row in pending:
            row.exported = True
        return pending
```

## Side-Effects `audit.json` Deliverable Example

Check this hidden-side-effects audit output with `python3 <this-skill>/scripts/validate_audit.py side-effects-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "side-effects-sample",
  "repository": {
    "identity": "acme/payout-console",
    "revision": "c3ab95d",
    "dirty": false,
    "snapshot": "git-tree:c3ab95d"
  },
  "scope": [
    "src/payouts/batch.py"
  ],
  "coverage": [
    {
      "topic": "side-effects",
      "scope": "src/payouts/batch.py",
      "status": "inspected",
      "evidence_ids": [
        "E-224"
      ],
      "notes": "Inspected get_pending_summary and callers in GET /payouts/preview and POST /payouts/export.",
      "next_action": "Hand off HTTP verb idempotency review to repo-audit-idempotency if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-224",
      "dimension": "side-effects",
      "scope": "PayoutBatch.get_pending_summary",
      "intended": "Previewing pending payouts leaves PayoutRow.exported unchanged for the subsequent export command.",
      "observed": "get_pending_summary sets row.exported = True on every pending row as a side effect of computing totals.",
      "enforcement": "violated by in-place mutation in src/payouts/batch.py",
      "evidence_ids": [
        "E-224"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-224",
      "path": "src/payouts/batch.py",
      "line_start": 16,
      "line_end": 22,
      "symbol": "PayoutBatch.get_pending_summary",
      "context": "Iterates pending rows and assigns row.exported = True before returning summary dictionary."
    }
  ],
  "findings": [
    {
      "id": "F-224",
      "topic": "side-effects",
      "tags": [
        "command-query-separation"
      ],
      "title": "Opening payout preview marks pending rows as exported and produces empty CSV download",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-224"
      ],
      "evidence_ids": [
        "E-224"
      ],
      "preconditions": "Operator views pending payout summary modal before clicking Download CSV on the same session batch.",
      "sequence": [
        "GET /payouts/preview calls batch.get_pending_summary(), displaying count=12 and setting row.exported=True.",
        "Operator clicks Download CSV, invoking batch.export_csv() which filters [r for r in rows if not r.exported].",
        "export_csv() finds zero unexported rows and downloads a header-only CSV."
      ],
      "expected": "Calling get_pending_summary() any number of times leaves pending rows available for export_csv().",
      "actual": "First summary read consumes pending state and empties subsequent CSV generation.",
      "impact": "Operators download empty bank payout files while batch state claims items were already exported.",
      "root_cause": "Query helper mutated state flag instead of separating read projection from export command.",
      "counterevidence": "Automated test calling export_csv() directly without previewing passes.",
      "remedy": "Remove row.exported mutation from get_pending_summary() and mutate only inside explicit commit/export command.",
      "acceptance": [
        "Calling get_pending_summary() twice followed by export_csv() yields identical counts and all 12 CSV rows."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace verified from preview route handler through PayoutBatch to export_csv."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
