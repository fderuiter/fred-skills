# Ownership and Lifecycle Boundaries: Specialist Method and Examples

## Pairing Acquisition Sites Against Teardown and Late-Resolution Paths

1. Inventory every long-lived handle—media tracks, `WebSocket` connections, `setInterval` timers, event listeners, file descriptors, worker pools, and DB cursors—pairing each acquisition call with its owning scope and disposal path.
2. Trace asynchronous acquisition races where teardown (`unmount`, `close`, exception unwind) executes *while* `open()`/`getUserMedia()`/`connect()` is still pending so the handle arrives after cleanup has already finished.
3. Separate genuine leaks from intentionally process-scoped singletons (such as a shared SQL connection pool closed only on SIGTERM) whose lifetime matches process ownership.

## Failure Trace vs. Safe Control: Async Media Stream Resolving After Component Unmount

```typescript
// Leak: if component unmounts while getUserMedia is pending, streamRef is still null during cleanup
useEffect(() => {
  let streamRef: MediaStream | null = null;
  navigator.mediaDevices.getUserMedia({ video: true }).then((stream) => {
    streamRef = stream; // arrives after unmount -> camera LED stays on!
  });
  return () => {
    streamRef?.getTracks().forEach((t) => t.stop());
  };
}, []);

// Safe control: disposed flag stops tracks immediately if acquisition finishes post-unmount
useEffect(() => {
  let disposed = false;
  let activeStream: MediaStream | null = null;
  navigator.mediaDevices.getUserMedia({ video: true }).then((stream) => {
    if (disposed) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    activeStream = stream;
  });
  return () => {
    disposed = true;
    activeStream?.getTracks().forEach((t) => t.stop());
  };
}, []);
```

## Lifecycle `audit.json` Deliverable Example

Validate this stream-handle ledger locally using `python3 <this-skill>/scripts/validate_audit.py lifecycle-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "lifecycle-sample",
  "repository": {
    "identity": "acme/telehealth-room",
    "revision": "6c019ba",
    "dirty": false,
    "snapshot": "git-tree:6c019ba"
  },
  "scope": [
    "src/components/CameraPreview.tsx"
  ],
  "coverage": [
    {
      "topic": "lifecycle",
      "scope": "src/components/CameraPreview.tsx",
      "status": "inspected",
      "evidence_ids": [
        "E-501"
      ],
      "notes": "Audited camera/microphone track acquisition and unmount disposal paths.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-501",
      "dimension": "lifecycle",
      "scope": "CameraPreview effect hook",
      "intended": "Unmounting CameraPreview releases all acquired camera MediaStreamTracks.",
      "observed": "Cleanup stops streamRef only if getUserMedia resolved before unmount.",
      "enforcement": "partial cleanup in src/components/CameraPreview.tsx",
      "evidence_ids": [
        "E-501"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-501",
      "path": "src/components/CameraPreview.tsx",
      "line_start": 24,
      "line_end": 36,
      "symbol": "CameraPreview",
      "context": "Cleanup callback reads streamRef before pending getUserMedia promise resolves."
    }
  ],
  "findings": [
    {
      "id": "F-501",
      "topic": "lifecycle",
      "tags": [
        "concurrency",
        "cancellation"
      ],
      "title": "Pending getUserMedia resolution after unmount leaks active camera track",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-501"
      ],
      "evidence_ids": [
        "E-501"
      ],
      "preconditions": "User closes preview modal before browser camera permission prompt or hardware open resolves.",
      "sequence": [
        "CameraPreview mounts and invokes getUserMedia({ video: true }).",
        "User dismisses modal; effect cleanup runs while streamRef is null.",
        "getUserMedia resolves and assigns streamRef without stopping tracks."
      ],
      "expected": "Late-resolving MediaStream tracks stop immediately when disposed === true.",
      "actual": "Hardware camera capture remains active for the remainder of the browser tab session.",
      "impact": "Privacy indicator stays illuminated and hardware camera remains locked after leaving call setup.",
      "root_cause": "Effect cleanup lacks a disposed latch checked upon async resource fulfillment.",
      "counterevidence": "Unmounting after video preview is already visible stops tracks cleanly.",
      "remedy": "Track a boolean disposed flag in the effect closure and stop all tracks immediately if getUserMedia settles after unmount.",
      "acceptance": [
        "Resolving deferred getUserMedia mock after unmount calls track.stop() on every returned track."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static lifecycle ownership trace verified."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
