# Using the audit suite

## Select scope

Use [repo-audit-program](../skills/repo-audit-program/SKILL.md) for broad audits. It
coordinates three passes and preserves evidence between them. Use a
[specialist](catalog.md) for a bounded investigation. Each specialist reconstructs
its own prerequisites when run alone; sibling skills are optional deeper handoffs.
Use [repo-audit-synthesis](../skills/repo-audit-synthesis/SKILL.md) to challenge and
consolidate completed findings into issue drafts and implementation workstreams.

Provide repository/revision, critical workflows, and an existing audit workspace if
available. Replace the plain skill name with your host's explicit invocation syntax
when appropriate. Follow its current installation/discovery instructions and test
behavior in that environment. Recognizing SKILL.md does not prove identical runtime
behavior, permissions, or tool availability across agents.

## Installation

Install all 35 audit skills directly via plugin marketplaces:

### Claude Code

```sh
/plugin marketplace add fderuiter/fred-skills
```

### Codex

```sh
codex plugin marketplace add fderuiter/fred-skills
```

For offline or manual workflows, copy skill directories directly to your host's skills path or use `python scripts/fred.py install`.

## Focused investigation

```text
Use repo-audit-concurrency to audit <repository> at <revision>, focusing on start,
cancel, replacement, recovery, and completion. Trace callers and effects, construct
deterministic event sequences, and inspect guards and synchronous callback boundaries.
Return evidence, refuted candidates, actual validation state, and remaining coverage.
Keep application code unchanged and produce issue recommendations as drafts.
```

Each specialist includes a standalone prompt in `assets/prompt.md`, a discovery and
validation method, a safe control, topic sources, and shared report/handoff templates.

## Resume a run

```text
Continue repo-audit-program using <audit workspace>. Compare its snapshot to <revision>.
Revalidate changed evidence and dependent findings, preserve existing IDs and unrelated
records, and finish the next unreviewed slice. Record a precise continuation point.
```

Keep `audit.json` and topic reports with the audited project or a private audit
workspace, rather than in this skill-source repository. The audit ledger holds source
snapshot, coverage, evidence, contracts, findings, checks, planned issues, and limitations.

## Validate an audit ledger

```sh
python skills/repo-audit-program/scripts/validate_audit.py /path/to/audit.json
```

This optional checker needs Python and jsonschema. It verifies structure, reference
integrity, classification consistency, and dependency cycles. It cannot establish
whether a finding is true or whether an audit covered all relevant behavior.

## Publish issues when authorized

```text
Use repo-audit-synthesis on <audit workspace> for <owner/repository> at <revision>.
Revalidate evidence and existing open/closed issues and PRs. Create justified,
nonduplicate issues with independent acceptance criteria, validation, verified links,
and actual blocker relationships. Leave application code unchanged and report failures.
```

Without that authorization, synthesis produces local drafts. Existing applicable
authorization does not require repeated confirmation. Unverified risks remain bounded
investigations; refuted candidates do not become defect issues. Distinct independently
fixable causes remain separate even if symptoms overlap.

## Download and verify release archives

Official releases publish standalone tarball packages for all 35 audit specialists
as well as a consolidated suite archive (`fred-skills-suite.tar.gz`), each accompanied by
a cryptographic SHA-256 entry in `checksums.sha256` and GitHub SLSA build provenance attestations.

### Download

Fetch release artifacts directly from GitHub Releases or via `curl`:

```sh
# Download a single specialist and checksum manifest
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/repo-audit-concurrency.tar.gz
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/checksums.sha256

# Or download the complete 35-skill audit suite
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-suite.tar.gz
```

### Verify checksums and attestations

Check artifact integrity and authenticate release origin:

```sh
# Verify SHA-256 checksum against manifest
sha256sum --ignore-missing -c checksums.sha256

# Verify cryptographic SLSA provenance attestation against repository origin
gh attestation verify repo-audit-concurrency.tar.gz --repo fderuiter/fred-skills
gh attestation verify fred-skills-suite.tar.gz --repo fderuiter/fred-skills
```

### Extract and deploy

Unpack verified archives into your agent host skills destination:

```sh
# Deploy single specialist (creates repo-audit-concurrency/ directory)
tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agent/skills/

# Deploy complete audit suite (creates all 35 repo-audit-*/ directories)
tar -xzf fred-skills-suite.tar.gz -C /path/to/project/.agent/skills/
```

