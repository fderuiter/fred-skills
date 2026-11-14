#!/usr/bin/env python3
"""Triage failed audit runs, scrub secrets, and export diagnostic bundles."""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import platform
import re
import sys

# Redaction patterns for credential scrubbing
CREDENTIAL_PATTERNS = [
    # Authorization header: Authorization: Bearer / Basic / Token ...
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)([^\s\r\n'\"]+)", re.IGNORECASE), r"\1[REDACTED_AUTH_TOKEN]"),
    (re.compile(r"(?i)\b(bearer\s+)([a-zA-Z0-9_\-\.]{16,})\b", re.IGNORECASE), r"\1[REDACTED_BEARER_TOKEN]"),
    # OpenAI / Anthropic / general API keys
    (re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"), "[REDACTED_API_KEY]"),
    # GitHub personal access tokens
    (re.compile(r"\b(gh[pousr]_[a-zA-Z0-9]{36,}|github_pat_[a-zA-Z0-9_]{30,})\b"), "[REDACTED_GITHUB_TOKEN]"),
    # Slack tokens
    (re.compile(r"\b(xox[baprs]-[a-zA-Z0-9_\-]{10,})\b"), "[REDACTED_SLACK_TOKEN]"),
    # AWS Access Key ID
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "[REDACTED_AWS_KEY]"),
    # JWT tokens (3 base64url segments separated by dots)
    (re.compile(r"\b(eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,})\b"), "[REDACTED_JWT_TOKEN]"),
    # Private Key blocks
    (re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    # Generic key-value secrets (quoted: password="...", api_key='...', secret: ...)
    (re.compile(r"(?i)\b((?:api[_-]?key|secret|password|passwd|token|auth[_-]?token)\s*[:=]\s*(['\"]))(.*?)\2"), r"\1[REDACTED_SECRET]\2"),
    # Generic key-value secrets (unquoted: password=..., token: ...)
    (re.compile(r"(?i)\b((?:api[_-]?key|secret|password|passwd|token|auth[_-]?token)\s*[:=]\s*)([^\s'\",;\r\n]+)"), r"\1[REDACTED_SECRET]"),
]

# Signatures for failure detection
TIMEOUT_PATTERNS = [
    re.compile(r"(?i)(?:command|process|tool|execution|query)\s+(?:timed out|has timed out)"),
    re.compile(r"(?i)timed out after \d+(?:\.\d+)?\s*(?:seconds|s|sec)"),
    re.compile(r"\bTimeoutError\b:?"),
    re.compile(r"(?i)\bdeadline exceeded\b"),
    re.compile(r"(?i)\bsubprocess\.TimeoutExpired\b"),
    re.compile(r"(?i)execution timed out after"),
]

SCHEMA_PATTERNS = [
    re.compile(r"\bjsonschema(?:\.exceptions)?\.ValidationError\b"),
    re.compile(r"'(?:schema_version|contracts|evidence|findings|issues)' is a required property"),
    re.compile(r"(?i)\bschema mismatch\b"),
    re.compile(r"(?i)\bschema validation (?:failed|error)\b"),
    re.compile(r"(?i)is not valid under any of the given schemas"),
    re.compile(r"Duplicate IDs in (?:evidence|contracts|findings|issues):"),
    re.compile(r"Reversed lines:"),
    re.compile(r"Evidence path must be repository-relative:"),
    re.compile(r"Unknown (?:evidence|contracts|findings|issues) reference"),
    re.compile(r"Actionable defect lacks contract/evidence:"),
    re.compile(r"Confirmed finding lacks executed command:"),
    re.compile(r"Refuted candidate must be info:"),
    re.compile(r"Issue dependency cycle:"),
]

CONTEXT_PATTERNS = [
    re.compile(r"(?i)\bcontext_length_exceeded\b"),
    re.compile(r"(?i)maximum context length is \d+ tokens"),
    re.compile(r"(?i)prompt is too long"),
    re.compile(r"(?i)prompt exceeds context window"),
    re.compile(r"(?i)token limit exceeded"),
    re.compile(r"(?i)context window limit"),
]

KNOWN_SPECIALISTS = [
    "repo-audit-behavioral-contracts",
    "repo-audit-invariants",
    "repo-audit-state-machines",
    "repo-audit-concurrency",
    "repo-audit-lifecycle",
    "repo-audit-temporal-coupling",
    "repo-audit-domain-models",
    "repo-audit-failure-modes",
    "repo-audit-trust-boundaries",
    "repo-audit-transactions",
    "repo-audit-idempotency",
    "repo-audit-error-semantics",
    "repo-audit-capacity",
    "repo-audit-cancellation",
    "repo-audit-cache-coherence",
    "repo-audit-data-integrity",
    "repo-audit-boundary-conditions",
    "repo-audit-protocol-drift",
    "repo-audit-configuration",
    "repo-audit-compatibility",
    "repo-audit-complexity",
    "repo-audit-canonicalization",
    "repo-audit-side-effects",
    "repo-audit-sources-of-truth",
    "repo-audit-architecture",
    "repo-audit-test-contracts",
    "repo-audit-cohesion",
    "repo-audit-observability",
    "repo-audit-nondeterminism",
    "repo-audit-test-oracles",
    "repo-audit-api-consistency",
    "repo-audit-accessibility-state",
    "repo-audit-dead-code",
    "repo-audit-synthesis",
    "repo-audit-program",
]


def scrub_credentials(text: str) -> str:
    """Scrub tokens, passwords, authorization headers, and keys from text."""
    scrubbed = text
    for pattern, replacement in CREDENTIAL_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def detect_failures(transcript_text: str) -> dict:
    """Scan transcript text line by line to detect timeouts, schema errors, or context limits."""
    lines = transcript_text.splitlines()

    # Search for decisive root-cause error lines
    for idx, line in enumerate(lines, start=1):
        # 1. Tool timeouts
        for pattern in TIMEOUT_PATTERNS:
            if pattern.search(line):
                return {
                    "failure_type": "tool_timeout",
                    "summary": f"Tool execution timed out: {line.strip()}",
                    "line_number": idx,
                    "matched_line": line.strip(),
                    "details": {"pattern": pattern.pattern},
                }

        # 2. Schema validation mismatches
        for pattern in SCHEMA_PATTERNS:
            if pattern.search(line):
                return {
                    "failure_type": "schema_validation_error",
                    "summary": f"Audit schema validation error: {line.strip()}",
                    "line_number": idx,
                    "matched_line": line.strip(),
                    "details": {"pattern": pattern.pattern},
                }

        # 3. Context window limits
        for pattern in CONTEXT_PATTERNS:
            if pattern.search(line):
                return {
                    "failure_type": "context_limit",
                    "summary": f"Context window token limit exceeded: {line.strip()}",
                    "line_number": idx,
                    "matched_line": line.strip(),
                    "details": {"pattern": pattern.pattern},
                }

    # If no known signature matched, check if there's any general error line
    for idx, line in enumerate(lines, start=1):
        if re.search(r"(?i)\b(?:fatal|traceback|error|exception)\b", line):
            return {
                "failure_type": "unclassified_failure",
                "summary": f"Unclassified execution failure: {line.strip()}",
                "line_number": idx,
                "matched_line": line.strip(),
                "details": {},
            }

    return {
        "failure_type": "unclassified_failure",
        "summary": "Session ended unexpectedly without an explicit error signature",
        "line_number": len(lines) if lines else 1,
        "matched_line": lines[-1].strip() if lines else "",
        "details": {},
    }


def correlate_audit_domain(transcript_text: str, audit_data: dict | None = None) -> dict:
    """Correlate failed transcript steps with specialist audit skills and ledger entries."""
    specialist_found = None
    topic_found = None

    for specialist in KNOWN_SPECIALISTS:
        if specialist in transcript_text:
            specialist_found = specialist
            topic_found = specialist.replace("repo-audit-", "")
            break

    if not specialist_found:
        # Search by topic keywords
        for specialist in KNOWN_SPECIALISTS:
            keyword = specialist.replace("repo-audit-", "")
            if keyword in transcript_text.lower():
                specialist_found = specialist
                topic_found = keyword
                break

    ledger_refs = []
    if audit_data:
        # Check if any IDs from audit.json appear in the transcript
        for section in ("findings", "evidence", "contracts", "issues"):
            for item in audit_data.get(section, []):
                item_id = item.get("id")
                if item_id and item_id in transcript_text:
                    ledger_refs.append(f"{section}:{item_id}")
    else:
        # Extract ID patterns directly from transcript
        id_pattern = re.compile(r"\b([FEIC]-\d+|E-[a-z0-9_-]+)\b")
        ledger_refs = sorted(set(id_pattern.findall(transcript_text)))

    return {
        "specialist_skill": specialist_found,
        "topic": topic_found,
        "ledger_references": ledger_refs,
    }


def formulate_recovery_instructions(
    failure: dict,
    run_data: dict | None = None,
    workspace_dir: Path | None = None,
) -> dict:
    """Generate concrete recovery steps to resume audit from the last valid checkpoint."""
    last_checkpoint = None
    instructions = []

    dir_str = str(workspace_dir) if workspace_dir else "<audit-run-dir>"

    if run_data and run_data.get("checkpoints"):
        last_ckpt = run_data["checkpoints"][-1]
        last_checkpoint = f"{last_ckpt.get('state', 'unknown')}: {last_ckpt.get('note', '')} (at {last_ckpt.get('at', '')})"
    elif run_data and run_data.get("state"):
        last_checkpoint = f"Run state: {run_data['state']}"

    ftype = failure.get("failure_type")
    if ftype == "tool_timeout":
        instructions.append(f"1. Check the command that timed out at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'.")
        instructions.append("2. Add file exclusion patterns or narrow the target scope in the tool arguments.")
        instructions.append(f"3. Mark current state paused: python scripts/fred.py checkpoint {dir_str} --state paused --note 'Paused after tool timeout triage'.")
        instructions.append(f"4. Re-run specialist with bounded scope or resume: python scripts/fred.py resume {dir_str}")

    elif ftype == "schema_validation_error":
        instructions.append(f"1. Inspect the schema validation error at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'.")
        instructions.append(f"2. Validate current audit.json ledger: python skills/repo-audit-program/scripts/validate_audit.py {dir_str}/audit.json")
        instructions.append("3. Correct duplicate IDs, reversed line ranges, or missing required fields in audit.json.")
        instructions.append(f"4. Re-verify ledger validity, then resume audit coordination: python scripts/fred.py resume {dir_str}")

    elif ftype == "context_limit":
        instructions.append(f"1. Context limit reached at line {failure.get('line_number')}.")
        instructions.append(f"2. Save current progress to checkpoint: python scripts/fred.py checkpoint {dir_str} --state paused --note 'Context limit reached; clearing context'.")
        instructions.append("3. Split the specialist analysis into smaller bounded workflow slices.")
        instructions.append(f"4. Start fresh agent session and resume from checkpoint: python scripts/fred.py resume {dir_str}")

    else:
        instructions.append(f"1. Review the unclassified error at line {failure.get('line_number')}: '{failure.get('matched_line', '')}'.")
        instructions.append(f"2. Inspect {dir_str}/run.json and {dir_str}/audit.json for partial progress.")
        instructions.append(f"3. Resume audit run from the last valid checkpoint: python scripts/fred.py resume {dir_str}")

    return {
        "last_checkpoint": last_checkpoint,
        "instructions": instructions,
    }


def generate_diagnostic_bundle(
    transcript_path: Path,
    workspace_dir: Path | None = None,
    session_id: str | None = None,
) -> dict:
    """Generate a full, scrubbed diagnostic bundle from a session transcript."""
    raw_text = transcript_path.read_text(encoding="utf-8", errors="replace")

    # Read workspace state if available
    run_data = None
    audit_data = None
    if workspace_dir:
        run_file = workspace_dir / "run.json"
        if run_file.is_file():
            try:
                run_data = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        audit_file = workspace_dir / "audit.json"
        if audit_file.is_file():
            try:
                audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    resolved_session_id = session_id or (run_data.get("run_id") if run_data else None) or transcript_path.stem

    # Detect failures first on raw text
    failure = detect_failures(raw_text)

    # Correlate domain and ledger entries
    correlated = correlate_audit_domain(raw_text, audit_data)

    # Generate actionable recovery steps
    recovery = formulate_recovery_instructions(failure, run_data, workspace_dir)

    # Scrub all sensitive credentials from transcript and failure matched line
    scrubbed_transcript = scrub_credentials(raw_text)
    failure["matched_line"] = scrub_credentials(failure["matched_line"])
    failure["summary"] = scrub_credentials(failure["summary"])

    bundle = {
        "schema_version": "1.0",
        "session_id": resolved_session_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "failure_classification": failure,
        "correlated_domain": correlated,
        "environment": {
            "platform": f"{platform.system()} {platform.release()}",
            "python_version": sys.version.split()[0],
            "repository_path": run_data.get("repository", {}).get("path") if run_data else None,
            "run_id": run_data.get("run_id") if run_data else None,
        },
        "recovery": recovery,
        "scrubbed_transcript": scrubbed_transcript,
    }

    return bundle


def render_report(bundle: dict) -> str:
    """Format human-readable markdown report from bundle."""
    fc = bundle["failure_classification"]
    cd = bundle.get("correlated_domain", {})
    rec = bundle.get("recovery", {})

    instructions_md = "\n".join(rec.get("instructions", []))
    ledger_refs = ", ".join(cd.get("ledger_references", [])) or "None identified"

    return f"""# Diagnostic Triage Report

- **Session / Run ID:** {bundle.get('session_id', 'unknown')}
- **Timestamp:** {bundle.get('timestamp', 'unknown')}
- **Failure Classification:** {fc.get('failure_type', 'unknown')}
- **Root Cause Line:** {fc.get('line_number', 'unknown')}
- **Correlated Domain:** {cd.get('specialist_skill') or 'None identified'}

## Summary of failure

{fc.get('summary', '')}

```
{fc.get('matched_line', '')}
```

## Audit domain and ledger correlation

- **Specialist Skill:** {cd.get('specialist_skill') or 'None identified'}
- **Domain Topic:** {cd.get('topic') or 'None identified'}
- **Correlated Ledger Entries:** {ledger_refs}

## Actionable recovery instructions

{instructions_md}

## Sanitization status

All private credentials, tokens, authorization headers, and environment secrets
have been redacted from this bundle.
"""


def export_bundle(bundle: dict, output_dir: Path) -> tuple[Path, Path]:
    """Export diagnostic bundle JSON and Markdown report to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "diagnostic_bundle.json"
    report_path = output_dir / "diagnostic_report.md"

    json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(render_report(bundle), encoding="utf-8")

    return json_path, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcript", type=Path, help="Session transcript file (.json, .jsonl, .txt, .md)")
    parser.add_argument("--workspace", type=Path, help="Optional audit run workspace directory with run.json/audit.json")
    parser.add_argument("--output-dir", type=Path, help="Directory to write scrubbed diagnostic_bundle.json and report")
    parser.add_argument("--session-id", help="Explicit session or run ID")
    parser.add_argument("--check-schema", action="store_true", help="Validate generated bundle against bundle.schema.json")

    args = parser.parse_args()

    if not args.transcript.is_file():
        print(f"Error: Transcript file not found: {args.transcript}", file=sys.stderr)
        return 2

    bundle = generate_diagnostic_bundle(args.transcript, args.workspace, args.session_id)

    if args.check_schema:
        schema_path = Path(__file__).resolve().parents[1] / "assets/bundle.schema.json"
        if schema_path.is_file():
            try:
                import jsonschema
                jsonschema.validate(bundle, json.loads(schema_path.read_text(encoding="utf-8")))
            except ImportError:
                print("Warning: jsonschema not installed, skipping schema validation", file=sys.stderr)
            except Exception as e:
                print(f"Schema validation error: {e}", file=sys.stderr)
                return 1

    if args.output_dir:
        json_path, report_path = export_bundle(bundle, args.output_dir)
        print(f"Exported diagnostic bundle: {json_path}")
        print(f"Exported diagnostic report: {report_path}")
    else:
        print(json.dumps(bundle, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
