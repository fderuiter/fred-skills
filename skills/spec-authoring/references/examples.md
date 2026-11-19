# Specification Authoring Worked Examples

## Example 1: Translating Interview Notes into Falsifiable Requirements

Given settled pre-code notes for an NDJSON audit-log exporter (`src/exporter/ndjson.py`),
replace vague prose with testable `REQ-*` invariants and explicit scope locks:

```python
# Existing interface in src/exporter/cursor.py inspected prior to drafting SPEC.md
class KeysetCursor:
    """Opaque base64url cursor encoding (created_at_ns: int, event_id: str)."""

    @classmethod
    def decode(cls, token: str) -> tuple[int, str]: ...
```

- **Rejected draft (`status: "draft"`)**: Leaves `verification_commands` empty and states "Exporter should stream large logs efficiently" without memory or chunk bounds.
- **Approved contract (`status: "approved"`)**: Pins 500-row keyset chunks via `KeysetCursor`, locks `new_file_scopes` to `src/exporter/ndjson.py` and `tests/test_ndjson_exporter.py`, and lists `src/exporter/legacy_csv.py` in `out_of_scope`.

## Example 2: Approved Feature Specification Contract (`spec.json`)

Emit synchronized `SPEC.md` (`assets/spec-template.md`) and `spec.json` (`assets/spec.schema.json`),
then verify locally via `python3 <this-skill>/scripts/validate_spec.py spec.json`:

```json
{
  "schema_version": "1.0",
  "title": "Streaming NDJSON Event Exporter",
  "status": "approved",
  "summary": "Cursor-paginated NDJSON export endpoint bounded to 500-event chunks with deterministic mid-stream error trailers.",
  "interview_decisions": [
    {
      "id": "DEC-01",
      "category": "ux",
      "question": "How should mid-stream read errors be surfaced after HTTP 200 headers are flushed?",
      "decision": "Emit a final NDJSON object {\"type\": \"error\", \"code\": \"stream_aborted\"} and close the stream.",
      "rationale": "Callers parsing line-delimited JSON can detect truncation deterministically."
    },
    {
      "id": "DEC-02",
      "category": "architecture",
      "question": "Which pagination authority prevents duplicate or skipped rows under concurrent writes?",
      "decision": "Use KeysetCursor over (created_at_ns, event_id) with fixed 500-row database fetches.",
      "rationale": "Avoids OFFSET scan degradation and guarantees monotonic progress."
    }
  ],
  "requirements": [
    {
      "id": "REQ-01",
      "description": "Stream events in batches of at most 500 rows using KeysetCursor without buffering the full result set.",
      "priority": "must"
    },
    {
      "id": "REQ-02",
      "description": "Append a terminal error JSON line when storage iteration fails after stream start.",
      "priority": "must"
    },
    {
      "id": "REQ-03",
      "description": "Include X-Next-Cursor response trailer when additional pages remain.",
      "priority": "should"
    }
  ],
  "new_file_scopes": [
    "src/exporter/ndjson.py",
    "src/exporter/routes.py",
    "tests/test_ndjson_exporter.py"
  ],
  "out_of_scope": [
    "src/exporter/legacy_csv.py",
    "Cross-tenant export aggregation and S3 archival"
  ],
  "verification_commands": [
    "python -m unittest tests/test_ndjson_exporter.py -v"
  ]
}
```
