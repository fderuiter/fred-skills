# Duplicated Sources of Truth: Specialist Method and Examples

## Detecting Divergent Parallel Authorities and Denormalized Counters

1. Map every domain fact stored in more than one place: boolean `is_complete` columns alongside child status tables, denormalized `completed_chunks` integers alongside `received_chunk_ids` sets, or duplicated permission maps across gateway and service configs.
2. Trace retries, partial failures, out-of-order deliveries, and manual admin edits to determine whether one representation can advance while its sibling stays behind—and check which authority wins at security or completion gates.
3. Distinguish disposable read projections backed by atomic triggers or deterministic reconciliation jobs from uncoordinated dual writes where neither copy is designated authoritative.

## Failure Trace vs. Safe Control: Retry-Inflated Counter Completing Upload With Missing Chunks

```python
# Defect trace: duplicate chunk retry increments completed_count even when chunk_index was already recorded
class UploadSession:
    def record_chunk_buggy(self, chunk_index: int) -> bool:
        self.received_indices.add(chunk_index)
        self.completed_count += (
            1  # diverges from len(received_indices) when chunk 0 arrives twice!
        )
        return self.completed_count >= self.expected_chunks


# Safe control: derive completion directly from authoritative received_indices set cardinality
class SingleTruthUploadSession:
    def record_chunk_safe(self, chunk_index: int) -> bool:
        if not (0 <= chunk_index < self.expected_chunks):
            raise ValueError(f"chunk_index out of range: {chunk_index}")
        self.received_indices.add(chunk_index)
        return len(self.received_indices) == self.expected_chunks
```

## Sources-of-Truth `audit.json` Output Example

Run `python3 <this-skill>/scripts/validate_audit.py sources-of-truth-audit.json` to validate against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "sources-of-truth-sample",
  "repository": {
    "identity": "acme/chunk-uploader",
    "revision": "d4bc06e",
    "dirty": false,
    "snapshot": "git-tree:d4bc06e"
  },
  "scope": [
    "src/uploads/session.py"
  ],
  "coverage": [
    {
      "topic": "sources-of-truth",
      "scope": "src/uploads/session.py",
      "status": "inspected",
      "evidence_ids": [
        "E-225"
      ],
      "notes": "Compared UploadSession.completed_count against UploadSession.received_indices across retry paths.",
      "next_action": "Hand off concurrent worker locking to repo-audit-concurrency if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-225",
      "dimension": "sources-of-truth",
      "scope": "UploadSession completion predicate",
      "intended": "Upload finalizes if and only if all expected_chunks distinct chunk indices [0..N-1] are present.",
      "observed": "Parallel completed_count increments unconditionally on duplicate chunk delivery and triggers premature finalization.",
      "enforcement": "split authority between completed_count and received_indices in src/uploads/session.py",
      "evidence_ids": [
        "E-225"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-225",
      "path": "src/uploads/session.py",
      "line_start": 22,
      "line_end": 28,
      "symbol": "UploadSession.record_chunk",
      "context": "Adds chunk_index to self.received_indices while unconditionally incrementing self.completed_count."
    }
  ],
  "findings": [
    {
      "id": "F-225",
      "topic": "sources-of-truth",
      "tags": [
        "data-integrity",
        "idempotency"
      ],
      "title": "Duplicate chunk retry inflates completed_count and finalizes truncated file",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-225"
      ],
      "evidence_ids": [
        "E-225"
      ],
      "preconditions": "3-chunk upload retries chunk 0 once before chunk 2 arrives.",
      "sequence": [
        "Client uploads chunk 0 (received={0}, completed_count=1) and retries chunk 0 (received={0}, completed_count=2).",
        "Client uploads chunk 1 (received={0,1}, completed_count=3 >= expected_chunks=3).",
        "Finalizer seals archive from chunks {0,1} while chunk 2 is missing."
      ],
      "expected": "Completion fires only when len(received_indices) == expected_chunks.",
      "actual": "Upload marks itself complete with missing chunk 2 and persists corrupt artifact.",
      "impact": "Network retries silently produce truncated customer uploads.",
      "root_cause": "Unreconciled denormalized counter maintained alongside authoritative chunk-ID set.",
      "counterevidence": "Lossless networks without chunk retries increment completed_count in lockstep with set size.",
      "remedy": "Eliminate completed_count and derive progress/completion from len(received_indices).",
      "acceptance": [
        "Delivering chunk 0 twice and chunk 1 once for expected_chunks=3 returns False until chunk 2 arrives."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static state-divergence trace verified across record_chunk and finalize_upload."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
