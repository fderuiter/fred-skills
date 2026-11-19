# Worked Examples: Feedback Synthesis

Load these examples on demand when deduplicating raw review observations, filtering false
positives, calibrating verdict/severity, and validating `review.json` with `scripts/validate_review.py`.

## 1. Raw Review Observations Requiring Deduplication and Safe-Control Filtering

```text
Raw note 1: src/http/upload.py:31 reads entire request stream into memory with await file.read() before checking MAX_UPLOAD_BYTES (OOM risk).
Raw note 2: src/http/upload.py:34 checks len(buf) > MAX_UPLOAD_BYTES too late after allocation (duplicate root cause of Raw note 1).
Raw note 3: src/http/upload.py:42 omits user authentication check (FALSE POSITIVE: router in src/http/app.py:18 mounts upload_router with dependencies=[Depends(require_authenticated_user)]).
Raw note 4: src/http/upload.py:51 catches Exception and logs str(exc) without exc_info or error code on S3 timeout.
```

Synthesis decision:
- Merge Raw notes 1 and 2 into single `high` finding `REV-001` with a concrete streaming chunk
  remediation.
- Drop Raw note 3 because router-level `Depends(require_authenticated_user)` enforces auth.
- Keep Raw note 4 as `low` finding `REV-002`, setting overall `verdict` to `"request_changes"`
  (note that `"approve"` is rejected by `validate_review.py` when `high` or `critical` findings
  are present).

## 2. Valid Synthesized Review Deliverable (`assets/review.schema.json`)

Validate locally with `python3 <this-skill>/scripts/validate_review.py review.json`:

```json
{
  "schema_version": "1.0",
  "verdict": "request_changes",
  "summary": "Streaming size enforcement is needed in src/http/upload.py before buffering uploads in memory; auth middleware coverage on upload_router was verified intact.",
  "findings": [
    {
      "id": "REV-001",
      "title": "Unbounded await file.read() allocates full payload before MAX_UPLOAD_BYTES check",
      "severity": "high",
      "file": "src/http/upload.py",
      "line": 31,
      "description": "Calling await file.read() buffers arbitrarily large multipart bodies in worker RAM before line 34 compares len(buf) > MAX_UPLOAD_BYTES, allowing a single oversized upload to trigger worker OOM.",
      "recommendation": "Read chunks in bounded 64 KiB increments while accumulating byte count and abort with HTTP 413 immediately once total_bytes exceeds MAX_UPLOAD_BYTES."
    },
    {
      "id": "REV-002",
      "title": "Broad except Exception discards storage error type and traceback context",
      "severity": "low",
      "file": "src/http/upload.py",
      "line": 51,
      "description": "Catching bare Exception and logging only str(exc) hides upstream S3 error codes and stack frames needed to distinguish transient timeouts from permission errors.",
      "recommendation": "Catch botocore ClientError/BotoCoreError specifically and pass exc_info=True with structured upload_id metadata."
    }
  ]
}
```
