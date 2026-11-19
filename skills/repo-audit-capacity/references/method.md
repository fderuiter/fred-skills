# Resource Bounds and Backpressure: Specialist Method and Examples

## Auditing Arrival-Rate vs. Drain-Rate Ceilings

1. Enumerate every in-memory buffer, channel, thread/task pool, upload accumulator, and batch list between external ingress and downstream I/O sinks.
2. Trace what happens when upstream arrival rate (`lambda`) exceeds worker drain throughput (`mu`) during downstream latency spikes: verify whether memory growth is capped by an explicit capacity limit and backpressure policy.
3. Differentiate bounded ring buffers or semaphore-gated queues (with documented drop-oldest, `429 Too Many Requests`, or caller-await semantics) from `asyncio.Queue()` / unbounded array pushes that grow until process OOM.

## Failure Trace vs. Safe Control: Unbounded Telemetry Frame Queue Under Slow Sink

```python
# Defect trace: unbounded Queue buffers arbitrary incoming frames when metrics sink stalls
class FrameIngestor:
    def __init__(self) -> None:
        self.pending: asyncio.Queue[bytes] = asyncio.Queue()  # maxsize=0 (unbounded!)

    async def on_frame(self, payload: bytes) -> None:
        await self.pending.put(payload)


# Safe control: cap payload size and queue depth, shedding excess load with explicit 429 status
class BoundedFrameIngestor:
    def __init__(self) -> None:
        self.pending: asyncio.Queue[bytes] = asyncio.Queue(maxsize=256)

    def on_frame(self, payload: bytes) -> None:
        if len(payload) > 65_536:
            raise PayloadTooLargeError(len(payload))
        try:
            self.pending.put_nowait(payload)
        except asyncio.QueueFull as exc:
            raise BackpressureRejectedError("ingest queue full") from exc
```

## Capacity and Backpressure `audit.json` Example

Check this capacity audit output with `python3 <this-skill>/scripts/validate_audit.py capacity-audit.json` against `assets/audit.schema.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "capacity-sample",
  "repository": {
    "identity": "acme/telemetry-gateway",
    "revision": "23cd95f",
    "dirty": false,
    "snapshot": "git-tree:23cd95f"
  },
  "scope": [
    "src/ingest/frame_ingestor.py"
  ],
  "coverage": [
    {
      "topic": "capacity",
      "scope": "src/ingest/frame_ingestor.py",
      "status": "inspected",
      "evidence_ids": [
        "E-213"
      ],
      "notes": "Inspected WebSocket frame reader, pending asyncio.Queue, and batch flusher loop.",
      "next_action": "Hand off worker task shutdown inspection to repo-audit-lifecycle if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-213",
      "dimension": "capacity",
      "scope": "FrameIngestor.pending",
      "intended": "Gateway memory stays bounded when downstream storage flushes stall.",
      "observed": "asyncio.Queue() defaults to maxsize=0, accumulating raw frame bytes without backpressure until OOM.",
      "enforcement": "none; no maxsize or byte budget in src/ingest/frame_ingestor.py",
      "evidence_ids": [
        "E-213"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-213",
      "path": "src/ingest/frame_ingestor.py",
      "line_start": 11,
      "line_end": 18,
      "symbol": "FrameIngestor.__init__",
      "context": "Constructs asyncio.Queue() without maxsize and appends incoming WebSocket frames unconditionally."
    }
  ],
  "findings": [
    {
      "id": "F-213",
      "topic": "capacity",
      "tags": [
        "resource-bounds"
      ],
      "title": "Unbounded frame queue exhausts process memory during downstream flush stalls",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-213"
      ],
      "evidence_ids": [
        "E-213"
      ],
      "preconditions": "Downstream ClickHouse batch write stalls for 60 seconds while clients stream 64 KiB frames.",
      "sequence": [
        "Batch flusher blocks awaiting slow ClickHouse response.",
        "WebSocket handlers continue calling FrameIngestor.on_frame() at 500 frames/sec.",
        "Unbounded pending queue retains ~1.9 GiB of buffered bytes and triggers container OOM kill."
      ],
      "expected": "Queue enforces bounded maxsize and byte cap, signaling WebSocket backpressure or HTTP 429.",
      "actual": "Memory grows linearly with stall duration until OOM termination drops all active connections.",
      "impact": "Single downstream latency spike crashes the multi-tenant ingest gateway.",
      "root_cause": "asyncio.Queue initialized with unbounded default maxsize=0 and no per-frame size check.",
      "counterevidence": "When ClickHouse responds within 10 ms, queue depth stays below 20 frames.",
      "remedy": "Configure bounded Queue(maxsize=256), cap per-frame byte length, and apply explicit backpressure on QueueFull.",
      "acceptance": [
        "Stalled sink test with 1,000 incoming frames caps queue length at 256 and surfaces BackpressureRejectedError."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static inspection of FrameIngestor queue construction and WebSocket producer loop."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
