# Algorithmic Complexity: Specialist Method and Examples

## Binding Nested Work to Untrusted or Growing Input Cardinalities

1. Express loop counts, string/byte reallocations, ORM round-trips, and regex backtracking in terms of concrete runtime variables (`N` uploaded chunks, `M` ACL rules, `K` webhook headers) and trace the maximum reachable size of each variable.
2. Target quadratic or superlinear amplification along hot or user-controlled paths: repeated array `includes`/`indexOf` inside loops, `buffer = buffer + chunk` reallocation per packet, `N+1` SQL queries per response row, or catastrophic regex alternation.
3. Do not flag nested loops bounded by small compile-time constants (for instance, iterating `3` RGB channels or `4` fixed priority buckets).

## Failure Trace vs. Safe Control: Quadratic Byte Concatenation on Streaming Upload vs. Linear Buffer Join

```python
# Defect trace: concatenating immutable bytes on each 4 KiB chunk copies O(N^2) total bytes for N chunks
def assemble_upload_buggy(chunks: Iterable[bytes]) -> bytes:
    payload = b""
    for chunk in chunks:
        payload = (
            payload + chunk
        )  # 50 MiB upload (~12,800 chunks) copies ~320 GiB of transient bytes!
    return payload


# Safe control: accumulate chunk references in list (or bytearray) with bounded byte cap and join once in O(N)
def assemble_upload_safe(chunks: Iterable[bytes], max_bytes: int = 52_428_800) -> bytes:
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(total)
        parts.append(chunk)
    return b"".join(parts)
```

## Complexity `audit.json` Deliverable Example

Validate this complexity finding locally with `python3 <this-skill>/scripts/validate_audit.py complexity-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "complexity-sample",
  "repository": {
    "identity": "acme/artifact-proxy",
    "revision": "a1ef73b",
    "dirty": false,
    "snapshot": "git-tree:a1ef73b"
  },
  "scope": [
    "src/uploads/assembler.py"
  ],
  "coverage": [
    {
      "topic": "complexity",
      "scope": "src/uploads/assembler.py",
      "status": "inspected",
      "evidence_ids": [
        "E-222"
      ],
      "notes": "Bound chunk count N to 50 MiB ingress limit divided by 4 KiB stream read size (~12,800 iterations).",
      "next_action": "Hand off memory ceiling enforcement to repo-audit-capacity if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-222",
      "dimension": "complexity",
      "scope": "assemble_upload",
      "intended": "Assembling permitted 50 MiB multipart uploads runs in O(N) time without blocking the event loop.",
      "observed": "Repeated immutable bytes concatenation copies O(N^2) cumulative bytes as chunk count N grows.",
      "enforcement": "quadratic reallocation inside loop in src/uploads/assembler.py",
      "evidence_ids": [
        "E-222"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-222",
      "path": "src/uploads/assembler.py",
      "line_start": 14,
      "line_end": 19,
      "symbol": "assemble_upload",
      "context": "Executes payload = payload + chunk inside per-chunk stream loop up to configured 50 MiB limit."
    }
  ],
  "findings": [
    {
      "id": "F-222",
      "topic": "complexity",
      "tags": [
        "performance",
        "dos"
      ],
      "title": "Quadratic immutable-bytes concatenation stalls worker CPU on multi-megabyte chunked uploads",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-222"
      ],
      "evidence_ids": [
        "E-222"
      ],
      "preconditions": "Client streams a permitted 50 MiB archive in 1 KiB–4 KiB chunks.",
      "sequence": [
        "Ingress handler passes chunk iterator (~12,800 chunks of 4 KiB) to assemble_upload.",
        "Each iteration allocates a fresh bytes object of size k * 4 KiB and copies all prior bytes.",
        "Worker spends seconds in memcpy allocations (~320 GiB copied for 50 MiB payload), starving sibling requests."
      ],
      "expected": "Upload assembly scales linearly O(total_bytes) via bytearray or list-collect + b''.join.",
      "actual": "CPU time scales quadratically with chunk count N, allowing small-chunk streams to stall workers.",
      "impact": "Adversarial or slow-dripping 1 KiB chunked uploads saturate proxy CPU well below byte quota.",
      "root_cause": "Reassigning payload = payload + chunk prevents CPython in-place buffer reuse or deferred join.",
      "counterevidence": "Small 64 KiB test uploads (~16 chunks) complete in microseconds.",
      "remedy": "Collect chunks in a list with running byte limit and call b''.join(parts) once.",
      "acceptance": [
        "Benchmarking 50,000 x 1 KiB chunks completes in <50 ms with linear scaling from 5,000 to 50,000 chunks."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Derived exact byte-copy summation N*(N+1)/2 * chunk_size from assembler.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
