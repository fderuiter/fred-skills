# Security-Sensitive Canonicalization: Specialist Method and Examples

## Comparing Validator Normalization Against Sink Resolution

1. Locate every security gate that inspects paths, URLs, hostnames, email domains, OAuth redirect URIs, or signed payloads before passing the input to a filesystem, HTTP client, router, or cryptographic sink.
2. Test parser differentials between the check and the sink: `startswith` on unparsed URLs (`https://trusted.example@attacker.test`), percent-decoding after `..` checks (`%2e%2e%2f`), Unicode NFKC/case-folding mismatches, and duplicate JSON keys.
3. Confirm safe parse-once designs where the authoritative parser (`Path.resolve()` + `is_relative_to`, `urllib.parse.urlsplit` / WHATWG `URL` with explicit `scheme` and `hostname` equality) produces a typed capability passed directly to the sink.

## Failure Trace vs. Safe Control: Prefix Check on Raw Webhook URL vs. Parsed Hostname Allowlist

```python
# Defect trace: string prefix check passes userinfo URL whose actual HTTP destination host is evil.example
def validate_callback_url_buggy(raw_url: str) -> str:
    if not raw_url.startswith("https://hooks.acme.internal/"):
        raise ValueError("untrusted callback")
    return (
        raw_url  # "https://hooks.acme.internal@evil.example/pwn" -> ValueError? Wait:
    )
    # "https://hooks.acme.internal.evil.example/pwn" starts with "https://hooks.acme.internal" if slash omitted,
    # or "https://hooks.acme.internal/..\\@evil.example" in permissive WHATWG URL parsers!


# Concrete parser differential: unnormalized path traversal check before urllib.parse.unquote
from pathlib import Path
from urllib.parse import unquote


def resolve_download_buggy(base_dir: Path, encoded_rel_path: str) -> Path:
    if ".." in encoded_rel_path:
        raise PermissionError("traversal blocked")
    return base_dir / unquote(
        encoded_rel_path
    )  # "%2e%2e/secrets.env" bypasses ".." check!


# Safe control: decode once, resolve canonical filesystem path, and enforce is_relative_to(base_root)
def resolve_download_safe(base_dir: Path, encoded_rel_path: str) -> Path:
    base_root = base_dir.resolve()
    candidate = (base_root / unquote(encoded_rel_path)).resolve()
    if not candidate.is_relative_to(base_root):
        raise PermissionError("path escapes base directory")
    return candidate
```

## Canonicalization `audit.json` Output Example

Verify this canonicalization audit ledger via `python3 <this-skill>/scripts/validate_audit.py canonicalization-audit.json` (`assets/audit.schema.json`):

```json
{
  "schema_version": "1.0",
  "run_id": "canonicalization-sample",
  "repository": {
    "identity": "acme/asset-server",
    "revision": "b2fa84c",
    "dirty": false,
    "snapshot": "git-tree:b2fa84c"
  },
  "scope": [
    "src/downloads/path_guard.py"
  ],
  "coverage": [
    {
      "topic": "canonicalization",
      "scope": "src/downloads/path_guard.py",
      "status": "inspected",
      "evidence_ids": [
        "E-223"
      ],
      "notes": "Traced URL-encoded path parameter from HTTP route through resolve_download to open().",
      "next_action": "Hand off authz tenant bucket checks to repo-audit-trust-boundaries if needed."
    }
  ],
  "contracts": [
    {
      "id": "C-223",
      "dimension": "canonicalization",
      "scope": "resolve_download",
      "intended": "Download handler serves files strictly inside base_dir regardless of URL percent-encoding.",
      "observed": "Guard checks '..' on raw percent-encoded string before unquote(), permitting '%2e%2e/' traversal.",
      "enforcement": "pre-decode substring check in src/downloads/path_guard.py",
      "evidence_ids": [
        "E-223"
      ]
    }
  ],
  "evidence": [
    {
      "id": "E-223",
      "path": "src/downloads/path_guard.py",
      "line_start": 15,
      "line_end": 21,
      "symbol": "resolve_download",
      "context": "Checks if '..' in encoded_rel_path prior to calling unquote(encoded_rel_path) and joining with base_dir."
    }
  ],
  "findings": [
    {
      "id": "F-223",
      "topic": "canonicalization",
      "tags": [
        "security",
        "path-traversal"
      ],
      "title": "Pre-unquote traversal check allows '%2e%2e/' escape outside asset base directory",
      "status": "supported",
      "severity": "critical",
      "confidence": "high",
      "contract_ids": [
        "C-223"
      ],
      "evidence_ids": [
        "E-223"
      ],
      "preconditions": "Caller requests /downloads?file=%2e%2e%2fconfig%2fsecrets.json where router passes raw query value.",
      "sequence": [
        "resolve_download checks '..' in '%2e%2e/config/secrets.json' -> False.",
        "unquote('%2e%2e/config/secrets.json') produces '../config/secrets.json'.",
        "base_dir / '../config/secrets.json' opens sibling secrets.json outside base_dir."
      ],
      "expected": "Canonical resolved path outside base_dir.resolve() raises PermissionError.",
      "actual": "Percent-encoded parent segments bypass substring guard and read arbitrary readable files.",
      "impact": "Unauthenticated or low-privileged users can read application configuration files outside asset root.",
      "root_cause": "Security predicate operated on uncanonicalized encoded string before unquote() transformation.",
      "counterevidence": "Literal '../secrets.json' is blocked by the substring check in existing unit tests.",
      "remedy": "Unquote once, call Path.resolve(), and enforce candidate.is_relative_to(base_dir.resolve()).",
      "acceptance": [
        "Inputs '../x', '%2e%2e/x', '%2e%2e%5cx', and symlink escapes all raise PermissionError."
      ],
      "validation": {
        "state": "not-run",
        "commands": [],
        "result": "Static dataflow verified from query parameter extraction through path_guard.py to open()."
      },
      "related_findings": []
    }
  ],
  "issues": [],
  "limitations": [],
  "checks": []
}
```
