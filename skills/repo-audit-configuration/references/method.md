# Configuration Combinatorics: Specialist Method and Examples

## Auditing Coupled Flags, Precedence Layers, and Live Reloads

1. Catalog environment variables, config files, CLI overrides, and runtime feature flags together with their precedence order and startup validation rules.
2. Focus on coupled configuration pairs (such as `ENABLE_ZSTD_EXPORT=true` paired with `STORAGE_ENCRYPTION_MODE=stream` or `AUTH_MODE=mtls` paired with `TLS_CLIENT_CA_PATH`) where enabling one knob without its companion breaks runtime invariants or weakens security.
3. Avoid combinatorial explosion over orthogonal presentation preferences; target coupled flags that alter wire formats, security gates, or background executors, and check whether startup validation rejects invalid pairs eagerly.

## Failure Trace vs. Safe Control: Coupled Compression + Signing Flags Without Startup Gate

```python
# Defect trace: enabling compressed webhook bodies without enabling raw-bytes HMAC signs pre-compression string while sending compressed bytes
def build_webhook_request_buggy(
    cfg: Config, json_body: str
) -> tuple[bytes, dict[str, str]]:
    sig = hmac_sha256(cfg.secret, json_body.encode("utf-8"))
    payload = (
        gzip.compress(json_body.encode("utf-8"))
        if cfg.compress_webhooks
        else json_body.encode("utf-8")
    )
    return payload, {
        "X-Signature": sig
    }  # receiver verifies HMAC over received gzipped bytes -> 401!


# Safe control: validate coupled config at startup and sign exact wire bytes emitted on transport
def validate_webhook_config(cfg: Config) -> None:
    if cfg.compress_webhooks and cfg.signature_scheme == "legacy-utf8-text":
        raise ConfigError(
            "compress_webhooks=True requires signature_scheme='wire-bytes-v2'"
        )
```

## Configuration `audit.json` Deliverable Example

Validate this configuration-combinatorics output with `python3 <this-skill>/scripts/validate_audit.py configuration-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "configuration-sample",
  "repository": {
    "identity": "acme/webhook-dispatcher",
    "revision": "89cd51f",
    "dirty": false,
    "snapshot": "git-tree:89cd51f"
  },
  "scope": [
    "src/webhooks/config.py",
    "src/webhooks/sender.py"
  ],
  "coverage": [
    {
      "topic": "configuration",
      "scope": "src/webhooks/sender.py",
      "status": "inspected",
      "evidence_ids": [
        "E-220"
      ],
      "notes": "Inspected Config schema defaults and interaction between compress_webhooks and signature computation.",
      "next_action": "Hand off signature canonicalization details to repo-audit-canonicalization if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-220",
      "dimension": "configuration",
      "scope": "Config.compress_webhooks + build_webhook_request",
      "intended": "Enabling compress_webhooks either signs wire-compressed bytes or fails config validation when paired with text-only signing.",
      "observed": "compress_webhooks=True signs uncompressed UTF-8 JSON while transmitting gzipped bytes without startup validation.",
      "enforcement": "none in Config.__post_init__ or sender.py",
      "evidence_ids": [
        "E-220"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-220",
      "path": "src/webhooks/sender.py",
      "line_start": 21,
      "line_end": 29,
      "symbol": "build_webhook_request",
      "context": "Computes hmac_sha256 over uncompressed json_body before conditionally gzip-compressing payload."
    }
  ],
  "findings": [
    {
      "id": "F-220",
      "topic": "configuration",
      "tags": [
        "config",
        "protocol-drift"
      ],
      "title": "Enabling WEBHOOK_COMPRESS=true breaks HMAC verification across all webhook deliveries",
      "status": "supported",
      "severity": "high",
      "confidence": "high",
      "contract_ids": [
        "C-220"
      ],
      "evidence_ids": [
        "E-220"
      ],
      "preconditions": "Operator sets WEBHOOK_COMPRESS=true while default SIGNATURE_SCHEME='legacy-utf8-text' remains active.",
      "sequence": [
        "Service boots without cross-field validation error on Config(compress_webhooks=True, signature_scheme='legacy-utf8-text').",
        "build_webhook_request signs uncompressed JSON string and gzip-compresses outbound HTTP body.",
        "Downstream receivers verify X-Signature over raw wire bytes and reject every delivery with 401."
      ],
      "expected": "Startup rejects incompatible flag combination or signs exact wire bytes per negotiated scheme.",
      "actual": "Service starts cleanly and emits 100% unverifiable webhook payloads.",
      "impact": "Toggling documented compression flag causes total webhook delivery outage until rollback.",
      "root_cause": "Independent boolean flag added to transport step without cross-validating signing stage.",
      "counterevidence": "Default WEBHOOK_COMPRESS=false leaves wire bytes identical to signed UTF-8 bytes.",
      "remedy": "Compute HMAC over final wire bytes or reject compress_webhooks with legacy-utf8-text at startup.",
      "acceptance": [
        "Pairwise test across compress_webhooks in {False, True} verifies receiver HMAC validation passes or invalid config fails boot."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static config-pair trace verified across config.py and sender.py."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
