# Boundary-Condition Correctness: Specialist Method and Examples

## Probing `0`, `1`, `N-1`, `N`, `N+1`, and Half-Open Slices

1. Inspect loop predicates (`<` vs. `<=`), cursor pagination offsets, chunk splitters, date/window endpoints, and empty-input branches at exact domain boundaries (`0`, `1`, `limit - 1`, `limit`, `limit + 1`).
2. Check whether sequential batch or page cursors cover the input stream as a partition without skipping or duplicating the boundary element when `len(rows) == page_size`.
3. Respect language and library half-open slice conventions (`items[start:end]` in Python/Go/JS where `end == len(items)` is an exclusive upper bound, not an out-of-bounds read).

## Failure Trace vs. Safe Control: Cursor Pagination Skipping Boundary Row on Exact Page Fill

```python
# Defect trace: advancing next_cursor by last_id + 1 skips valid contiguous IDs or mis-detects final page
def fetch_events_page_buggy(
    db: Database, after_id: int, limit: int
) -> tuple[list[Event], int | None]:
    rows = db.query(
        "SELECT * FROM events WHERE id > :after ORDER BY id ASC LIMIT :lim",
        after=after_id,
        lim=limit,
    )
    if len(rows) < limit:
        return rows, None
    return rows[:-1], rows[
        -1
    ].id  # drops rows[-1] because next query uses STRICT '>' on rows[-1].id!


# Safe control: fetch limit + 1 lookahead rows, return rows[:limit], and resume strictly after rows[limit - 1].id
def fetch_events_page_safe(
    db: Database, after_id: int, limit: int
) -> tuple[list[Event], int | None]:
    rows = db.query(
        "SELECT * FROM events WHERE id > :after ORDER BY id ASC LIMIT :lim",
        after=after_id,
        lim=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].id if (has_more and page) else None
    return page, next_cursor
```

## Boundary-Conditions `audit.json` Example

Check this boundary-condition ledger locally with `python3 <this-skill>/scripts/validate_audit.py boundary-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "boundary-conditions-sample",
  "repository": {
    "identity": "acme/audit-feed",
    "revision": "67ab39d",
    "dirty": false,
    "snapshot": "git-tree:67ab39d"
  },
  "scope": [
    "src/feed/pagination.py"
  ],
  "coverage": [
    {
      "topic": "boundary-conditions",
      "scope": "src/feed/pagination.py",
      "status": "inspected",
      "evidence_ids": [
        "E-217"
      ],
      "notes": "Evaluated fetch_events_page across 0, 1, limit-1, limit, and limit+1 row counts.",
      "next_action": "Hand off concurrent insertion ordering checks to repo-audit-api-consistency if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-217",
      "dimension": "boundary-conditions",
      "scope": "fetch_events_page",
      "intended": "Iterating pages until next_cursor is None yields every ordered event exactly once.",
      "observed": "When len(rows) == limit, returning rows[:-1] with cursor rows[-1].id and querying id > :after permanently skips rows[-1].",
      "enforcement": "broken off-by-one slice/cursor pairing in src/feed/pagination.py",
      "evidence_ids": [
        "E-217"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-217",
      "path": "src/feed/pagination.py",
      "line_start": 12,
      "line_end": 18,
      "symbol": "fetch_events_page",
      "context": "Queries LIMIT :lim with WHERE id > :after, then slices rows[:-1] while setting next_cursor = rows[-1].id."
    }
  ],
  "findings": [
    {
      "id": "F-217",
      "topic": "boundary-conditions",
      "tags": [
        "pagination",
        "off-by-one"
      ],
      "title": "Keyset pagination drops the Nth record at every full page boundary",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-217"
      ],
      "evidence_ids": [
        "E-217"
      ],
      "preconditions": "Total matching events >= limit (e.g. 50 events when limit=25).",
      "sequence": [
        "First call fetch_events_page(after_id=0, limit=25) loads IDs 1..25.",
        "Handler returns IDs 1..24 (rows[:-1]) and next_cursor=25 (rows[-1].id).",
        "Second call executes WHERE id > 25, starting at ID 26 and omitting event 25."
      ],
      "expected": "Sequential cursor iteration emits all 50 events without gaps.",
      "actual": "Event 25 (and every multiple of limit) is omitted from paginated results.",
      "impact": "Compliance export consumers silently miss 1 out of every N audit events.",
      "root_cause": "Lookahead sentinel queried LIMIT limit instead of LIMIT limit + 1 while advancing cursor past unreturned sentinel row.",
      "counterevidence": "Datasets with fewer than limit rows take the len(rows) < limit branch and return all rows.",
      "remedy": "Query LIMIT limit + 1, return rows[:limit], and set next_cursor = rows[limit - 1].id when len(rows) > limit.",
      "acceptance": [
        "Paginating 0, 1, 24, 25, 26, and 50 events with limit=25 yields the exact full sequence."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Traced exact index arithmetic for N=24, 25, 26 with limit=25."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
