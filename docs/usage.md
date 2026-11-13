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
