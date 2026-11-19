# Nondeterministic Behavior: Specialist Method and Examples

## Hunting Unseeded Randomness, Unordered Iteration, and Wall-Clock Races

1. Inspect hash/signature generators, cache-key builders, pagination queries lacking tie-breaker `ORDER BY` columns, wall-clock `datetime.now()` boundary checks, and `sleep`-based synchronization in production and test code.
2. Check whether two runs over identical logical inputs can produce different wire bytes, different winner records on ties, or flaky test verdicts across timezone/DST/CPU-load shifts.
3. Distinguish seeded replayable property-based test generators (which record and print their RNG seed for deterministic reproduction) from uncontrolled randomness or real-clock sleeps.

## Failure Trace vs. Safe Control: Unordered Set Iteration in Content Digest vs. Canonical Sorted Serialization

```python
# Defect trace: iterating Python set of scopes depends on per-process PYTHONHASHSEED, invalidating cache digests across workers
def compute_policy_digest_buggy(tenant_id: str, scopes: set[str]) -> str:
    raw = tenant_id + ":" + ",".join(scopes)  # order varies across worker processes!
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Safe control: sort deduplicated scopes deterministically before hashing
def compute_policy_digest_safe(tenant_id: str, scopes: set[str]) -> str:
    canonical = json.dumps(
        {"tenant_id": tenant_id, "scopes": sorted(scopes)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

## Nondeterminism `audit.json` Deliverable Example

Validate this nondeterminism ledger locally with `python3 <this-skill>/scripts/validate_audit.py nondeterminism-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "nondeterminism-sample",
  "repository": {
    "identity": "acme/policy-engine",
    "revision": "39ab51d",
    "dirty": false,
    "snapshot": "git-tree:39ab51d"
  },
  "scope": [
    "src/policy/digest.py"
  ],
  "coverage": [
    {
      "topic": "nondeterminism",
      "scope": "src/policy/digest.py",
      "status": "inspected",
      "evidence_ids": [
        "E-232"
      ],
      "notes": "Inspected ETag and Redis cache-key digest calculation over set[str] scope collections.",
      "next_action": "Hand off cache key collision review to repo-audit-cache-coherence if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-232",
      "dimension": "nondeterminism",
      "scope": "compute_policy_digest",
      "intended": "Identical (tenant_id, scopes) inputs produce identical SHA-256 ETag digests across all Gunicorn worker processes.",
      "observed": "','.join(scopes) iterates an unordered set[str] whose iteration order varies with per-process hash randomization.",
      "enforcement": "nondeterministic set iteration in src/policy/digest.py",
      "evidence_ids": [
        "E-232"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-232",
      "path": "src/policy/digest.py",
      "line_start": 11,
      "line_end": 15,
      "symbol": "compute_policy_digest",
      "context": "Joins scopes: set[str] directly without sorted() when computing SHA-256 policy ETag."
    }
  ],
  "findings": [
    {
      "id": "F-232",
      "topic": "nondeterminism",
      "tags": [
        "caching",
        "reproducibility"
      ],
      "title": "Unordered set iteration in compute_policy_digest produces divergent ETags across workers",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-232"
      ],
      "evidence_ids": [
        "E-232"
      ],
      "preconditions": "Multi-worker deployment runs default PYTHONHASHSEED=random with >=2 scopes per policy.",
      "sequence": [
        "Worker 1 hashes {'read', 'write'} as 'acme:read,write' and returns ETag W1.",
        "Client sends If-None-Match: W1 to load-balanced Worker 2.",
        "Worker 2 hashes {'read', 'write'} as 'acme:write,read' -> ETag W2 != W1, forcing full 200 recomputation."
      ],
      "expected": "compute_policy_digest({'read', 'write'}) returns identical hex digest regardless of PYTHONHASHSEED.",
      "actual": "Workers disagree on ETag for identical scope sets, causing perpetual conditional-GET misses.",
      "impact": "HTTP 304 caching and shared Redis policy lookups miss across worker processes.",
      "root_cause": " Serialization iterated unordered Python set without canonical sorting.",
      "counterevidence": "Single-process unit tests see consistent in-process set order within a single interpreter run.",
      "remedy": "Sort scopes via sorted(scopes) inside canonical JSON serialization before hashing.",
      "acceptance": [
        "Subprocess invocations with PYTHONHASHSEED=1 and PYTHONHASHSEED=2 emit identical digests."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified unordered set[str] type passed into compute_policy_digest."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
