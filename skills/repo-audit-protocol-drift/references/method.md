# Protocol Contract Drift: Specialist Method and Examples

## Comparing Producer Wire Encoding Against Consumer Framing

1. Pair every wire producer and consumer across repo boundaries—HTTP JSON routes vs. typed SDK clients, length-prefixed socket frames, webhook HMAC payloads, and event-bus schemas.
2. Compare field optionality, enum exhaustiveness, integer width, character vs. byte length headers, and error envelope shapes directly in both serializer and deserializer code rather than trusting shared docs.
3. Separate backward-compatible additive extensions (such as an optional JSON property ignored by tolerant readers) from breaking semantic or framing drift.

## Failure Trace vs. Safe Control: Codepoint Length Header vs. UTF-8 Byte Frame Reader

```typescript
// Defect trace: JS producer writes UTF-16 string.length in frame header while Go/Node byte reader slices UTF-8 bytes
function encodeFrameBuggy(message: string): Buffer {
  const body = Buffer.from(message, "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt32BE(message.length, 0); // "héllo" has message.length=5, body.byteLength=6!
  return Buffer.concat([header, body]);
}

// Safe control: frame header carries exact UTF-8 byte length matching consumer socket read
function encodeFrameSafe(message: string): Buffer {
  const body = Buffer.from(message, "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.byteLength, 0);
  return Buffer.concat([header, body]);
}
```

## Protocol-Drift `audit.json` Output Example

Verify this wire-protocol audit ledger with `python3 <this-skill>/scripts/validate_audit.py protocol-drift-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "protocol-drift-sample",
  "repository": {
    "identity": "acme/realtime-bridge",
    "revision": "78bc40e",
    "dirty": false,
    "snapshot": "git-tree:78bc40e"
  },
  "scope": [
    "packages/sdk-js/src/framing.ts",
    "services/router/frame_reader.go"
  ],
  "coverage": [
    {
      "topic": "protocol-drift",
      "scope": "packages/sdk-js/src/framing.ts",
      "status": "inspected",
      "evidence_ids": [
        "E-218",
        "E-219"
      ],
      "notes": "Compared JS SDK frame encoder header calculation against Go router byte-slice frame reader.",
      "next_action": "Hand off rolling client version skew analysis to repo-audit-compatibility if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-218",
      "dimension": "protocol-drift",
      "scope": "4-byte big-endian frame length header",
      "intended": "Producer writes exact UTF-8 payload byte count into the 4-byte uint32 frame prefix.",
      "observed": "JS SDK writes UTF-16 code-unit count (message.length) while Go router reads exact byte count via io.ReadFull.",
      "enforcement": "mismatched unit semantics between framing.ts and frame_reader.go",
      "evidence_ids": [
        "E-218",
        "E-219"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-218",
      "path": "packages/sdk-js/src/framing.ts",
      "line_start": 10,
      "line_end": 16,
      "symbol": "encodeFrame",
      "context": "Calls header.writeUInt32BE(message.length, 0) before appending Buffer.from(message, 'utf8')."
    },
    {
      "id": "E-219",
      "path": "services/router/frame_reader.go",
      "line_start": 28,
      "line_end": 37,
      "symbol": "ReadFrame",
      "context": "Allocates make([]byte, frameLen) and reads exact UTF-8 bytes from TCP stream."
    }
  ],
  "findings": [
    {
      "id": "F-218",
      "topic": "protocol-drift",
      "tags": [
        "wire-protocol",
        "encoding"
      ],
      "title": "Non-ASCII payloads desynchronize TCP frame stream due to UTF-16 length header drift",
      "status": "supported",
      "severity": "critical",
      "confidence": "high",
      "contract_ids": [
        "C-218"
      ],
      "evidence_ids": [
        "E-218",
        "E-219"
      ],
      "preconditions": "JS SDK sends any JSON message containing multibyte UTF-8 characters (accented text, CJK, emoji).",
      "sequence": [
        "SDK encodes JSON containing 'é' (1 UTF-16 code unit, 2 UTF-8 bytes) and writes N-1 into uint32 header.",
        "Go router reads N-1 bytes, truncating closing '}' and failing JSON unmarshal.",
        "Trailing '}' byte remains in TCP stream and corrupts the next frame's 4-byte length prefix."
      ],
      "expected": "Frame header encodes body.byteLength so multibyte UTF-8 messages parse without stream desync.",
      "actual": "First non-ASCII message truncates payload and corrupts subsequent frames on the connection.",
      "impact": "Internationalized client messages break persistent TCP sessions across the router.",
      "root_cause": "SDK used JavaScript string.length (UTF-16 code units) instead of encoded UTF-8 Buffer.byteLength.",
      "counterevidence": "ASCII-only test fixtures have string.length == byteLength and pass undetected.",
      "remedy": "Write body.byteLength into the uint32 header in encodeFrame and add multibyte round-trip vectors.",
      "acceptance": [
        "Round-tripping CJK and emoji frames across framing.ts and ReadFrame preserves stream alignment."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Cross-language trace verified between framing.ts and frame_reader.go."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
