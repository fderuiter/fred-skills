# Temporal Coupling: Specialist Method and Examples

## Auditing Multi-Step Initialization, Readiness Flags, and Teardown Order

1. Search for multi-phase setup protocols (`configure()`, `init()`, `connect()`, `start()`, `register()`, `warmUp()`) where callers must invoke methods in a hidden sequence before handling traffic.
2. Check whether readiness booleans or health-check endpoints flip to `ready = true` *before* awaited migrations, key loading, or downstream subscriptions finish, or whether concurrent callers trigger duplicate bootstrap runs.
3. Confirm whether constructors or typed factory functions (`await Service.connect(cfg)`) already make uninitialized instances unrepresentable before proposing API restructuring.

## Failure Trace vs. Safe Control: Premature Ready Flag Before Async Key Fetch Completes

```python
# Defect: self.ready flips True before await _load_jwks() finishes, exposing None verifier
class TokenVerifier:
    def __init__(self, jwks_url: str) -> None:
        self.jwks_url = jwks_url
        self.ready = False
        self._keys: dict[str, str] | None = None

    async def initialize(self) -> None:
        self.ready = True  # premature readiness publication!
        self._keys = await self._fetch_jwks(self.jwks_url)


# Safe control: async factory returns a verifier only after keys are loaded and immutable
class ReadyTokenVerifier:
    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    @classmethod
    async def create(cls, jwks_url: str) -> "ReadyTokenVerifier":
        keys = await cls._fetch_jwks(jwks_url)
        return cls(keys)
```

## Temporal-Coupling `audit.json` Deliverable Example

Validate this signer-bootstrap ledger locally using `python3 <this-skill>/scripts/validate_audit.py temporal-coupling-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "temporal-coupling-sample",
  "repository": {
    "identity": "acme/auth-gateway",
    "revision": "118da3f",
    "dirty": false,
    "snapshot": "git-tree:118da3f"
  },
  "scope": [
    "src/security/token_verifier.py",
    "src/server/startup.py"
  ],
  "coverage": [
    {
      "topic": "temporal-coupling",
      "scope": "src/security/token_verifier.py",
      "status": "inspected",
      "evidence_ids": [
        "E-601"
      ],
      "notes": "Traced startup sequence between HTTP listener bind, health probe, and JWKS bootstrap.",
      "next_action": "None"
    }
  ],
  "contracts": [
    {
      "id": "C-601",
      "dimension": "temporal-coupling",
      "scope": "TokenVerifier.initialize",
      "intended": "Verify requests are routed only after JWKS public keys are populated.",
      "observed": "initialize() sets self.ready = True prior to awaiting _fetch_jwks().",
      "enforcement": "misordered flag assignment in src/security/token_verifier.py",
      "evidence_ids": [
        "E-601"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-601",
      "path": "src/security/token_verifier.py",
      "line_start": 16,
      "line_end": 25,
      "symbol": "TokenVerifier.initialize",
      "context": "Sets self.ready = True on line 18 before await self._fetch_jwks() on line 19."
    }
  ],
  "findings": [
    {
      "id": "F-601",
      "topic": "temporal-coupling",
      "tags": [
        "concurrency",
        "lifecycle"
      ],
      "title": "Health check reports ready during JWKS fetch window, causing startup 500s",
      "status": "supported",
      "severity": "medium",
      "confidence": "high",
      "contract_ids": [
        "C-601"
      ],
      "evidence_ids": [
        "E-601"
      ],
      "preconditions": "Load balancer shifts traffic as soon as /readyz observes verifier.ready == True while JWKS HTTP fetch is in flight.",
      "sequence": [
        "startup.py spawns asyncio.create_task(verifier.initialize()).",
        "verifier.initialize() sets self.ready = True and suspends on _fetch_jwks().",
        "/readyz returns 200 OK; incoming request calls verify() and hits TypeError on self._keys is None."
      ],
      "expected": "/readyz stays 503 until self._keys is assigned (or server awaits async factory before binding port).",
      "actual": "Early requests fail with unhandled TypeError during rolling restarts.",
      "impact": "Startup error spike on every pod rollout.",
      "root_cause": "Two-step temporal initialization exposes partially constructed instance and flips ready flag before async completion.",
      "counterevidence": "After _fetch_jwks resolves, steady-state requests succeed.",
      "remedy": "Set self.ready = True only after self._keys assignment completes, and await verifier.initialize() before accepting ingress traffic.",
      "acceptance": [
        "Requests cannot reach verify() while _keys is None, and /readyz returns 503 until JWKS load completes."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static trace of startup.py and token_verifier.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
