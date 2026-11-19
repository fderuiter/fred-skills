# Untested Behavioral Contracts: Specialist Method and Examples

## Auditing Mocked Seams and Unverified Failure/Resume Guarantees

1. Map high-consequence behavioral promises (crash recovery, resumable transfers, authz denials, rollback cleanup, retry deduplication) against the actual test suite's exercised paths and mock boundaries.
2. Flag split-mocked integration seams where producer tests stub out the consumer and consumer tests feed idealized hand-written fixtures, leaving real boundary mismatches (off-by-one byte offsets, header casing, transaction commits) completely unexercised.
3. Measure behavioral contract coverage rather than raw line percentage: a 100%-line-covered module mocked at every collaborating seam can still fail its end-to-end resume contract.

## Failure Trace vs. Safe Control: Split-Mocked Resume Offset Seam vs. End-to-End Round-Trip Test

```python
# Defect trace: sender uses inclusive byte offset while receiver uses 0-based exclusive offset; isolated unit mocks pass both!
# sender.py: headers={"X-Resume-Byte": str(last_acked_index)}       # sends 1023 after 1024 bytes
# receiver.py: file.seek(int(headers["X-Resume-Byte"]))             # overwrites byte 1023 and shifts tail!

# Safe control: contract test exercises real Sender + Receiver across an interrupted stream without mocking wire headers
def test_interrupted_stream_resume_preserves_exact_sha256(tmp_path: Path) -> None:
    payload = os.urandom(4096)
    receiver = ChunkReceiver(tmp_path / "out.bin")
    sender = ChunkSender(
        payload, transport=FailOnceAtByteTransport(receiver, cut_at=1024)
    )
    sender.transfer_with_resume()
    assert (tmp_path / "out.bin").read_bytes() == payload
```

## Test-Contracts `audit.json` Output Example

Check this untested-contracts ledger locally with `python3 <this-skill>/scripts/validate_audit.py test-contracts-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "test-contracts-sample",
  "repository": {
    "identity": "acme/backup-agent",
    "revision": "f6de28a",
    "dirty": false,
    "snapshot": "git-tree:f6de28a"
  },
  "scope": [
    "src/transfer/sender.py",
    "src/transfer/receiver.py",
    "tests/test_transfer.py"
  ],
  "coverage": [
    {
      "topic": "test-contracts",
      "scope": "tests/test_transfer.py",
      "status": "inspected",
      "evidence_ids": [
        "E-228",
        "E-229"
      ],
      "notes": "Compared mocked X-Resume-Byte expectations in unit tests against sender.py and receiver.py.",
      "next_action": "Hand off assertion strength review to repo-audit-test-oracles if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-228",
      "dimension": "test-contracts",
      "scope": "Interrupted backup stream resume byte-identity",
      "intended": "Resuming an interrupted backup produces byte-for-byte identical output compared to an uninterrupted transfer.",
      "observed": "Unit tests mock transport on both sides with incompatible X-Resume-Byte conventions; zero tests run real resume.",
      "enforcement": "untested integration seam in tests/test_transfer.py",
      "evidence_ids": [
        "E-228",
        "E-229"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-228",
      "path": "src/transfer/sender.py",
      "line_start": 42,
      "line_end": 49,
      "symbol": "ChunkSender.resume",
      "context": "Slices payload[ack_bytes - 1:] while setting X-Resume-Byte to ack_bytes."
    },
    {
      "id": "E-229",
      "path": "tests/test_transfer.py",
      "line_start": 18,
      "line_end": 55,
      "symbol": "test_sender_unit / test_receiver_unit",
      "context": "Mocks transport in sender tests and passes synthetic chunks directly to receiver without crossing X-Resume-Byte seam."
    }
  ],
  "findings": [
    {
      "id": "F-228",
      "topic": "test-contracts",
      "tags": [
        "testing",
        "protocol-drift"
      ],
      "title": "Mocked transport seam masks 1-byte duplication bug during interrupted backup resume",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-228"
      ],
      "evidence_ids": [
        "E-228",
        "E-229"
      ],
      "preconditions": "Backup upload drops TCP connection mid-file and invokes ChunkSender.resume().",
      "sequence": [
        "Receiver acknowledges 1024 bytes written.",
        "ChunkSender.resume slices payload[1023:] while sending X-Resume-Byte: 1024.",
        "Receiver seeks to offset 1024 and writes duplicated byte 1023, corrupting archive checksum."
      ],
      "expected": "End-to-end interrupted-resume contract test catches any byte shift between sender and receiver.",
      "actual": "Isolated unit mocks assert each side's private assumption and pass while real resume corrupts files.",
      "impact": "Resumed customer backups fail checksum verification during disaster recovery.",
      "root_cause": "Critical protocol seam was tested only with hand-crafted mocks on each half.",
      "counterevidence": "Uninterrupted single-shot transfers never enter ChunkSender.resume().",
      "remedy": "Fix payload[ack_bytes:] slice and add an unmocked interrupted-stream round-trip contract test.",
      "acceptance": [
        "Unmocked sender+receiver round-trip across mid-stream disconnect matches SHA-256 of source payload."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Verified slice arithmetic in sender.py against seek offset in receiver.py and mock stubs in test_transfer.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
