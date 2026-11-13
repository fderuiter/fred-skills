---
name: repo-audit-canonicalization
description: "Audit security-sensitive canonicalization with code evidence and counterexamples. Use when auditing URL/path/Unicode normalization, ambiguous encodings, allowlist bypasses, or parser disagreement."
---

# Security-Sensitive Canonicalization

Investigate this contract dimension deeply and return evidence-backed findings.
Use the supplied source snapshot; do not assume a particular framework or deployment.

## Start

1. Read [shared protocol](references/protocol.md) once per run. Use its scope,
   evidence statuses, severity/confidence rubric, and application-code mutation boundary.
2. Read [investigation method](references/method.md). Inspect the listed inventory,
   callers, and safeguards before promoting a search match to a finding.
3. Reuse the existing audit.json and contract IDs if present. If running alone,
   establish a minimal run envelope and inspect required upstream contracts yourself.
   Related skills are optional deeper investigations, not mandatory dependency chains.

## Inventory

URL and path parsers, hostname allowlists, Unicode identifiers, decoding layers, filesystem resolution, and authorization keys.

## Investigation

1. Map raw input through each decoder, parser, normalization step, validator, resolver, authorization decision, and consuming sink.
2. Compare the representation validated with the representation used; check double decoding, userinfo, ports, redirects, IDNA, separators, case, and Unicode equivalence where relevant.
3. Check traversal and symlink/TOCTOU behavior using the actual filesystem semantics; textual prefix checks alone rarely establish containment.
4. Construct a minimal input that produces different security decisions across parsers; verify framework normalization and deployment behavior before claiming exploitability.
5. Recommend one authoritative parser and context-specific checks, not ad-hoc substring deletion or blanket Unicode rejection.

## Challenge and validate

Use this failure shape only as a discovery lead: A URL allowlist accepts a trusted hostname appearing in userinfo while the actual parsed destination is attacker controlled.

Actively test this counterexample: Unicode or percent encoding alone is not a vulnerability; require a reachable decision mismatch and sensitive consequence.

Use a table of raw, parsed, normalized, validated, and used values with accepted/rejected controls and redirect/resolution tests.

Check the repository's existing tooling before proposing commands. Read
[research references](references/sources.md) only when selecting a technique or
checking a framework/tool limitation. No listed tool is a prerequisite.

## Deliver and hand off

Produce the topic inventory and report using [report template](assets/report-template.md).
Record findings, healthy counterexamples, and coverage in audit.json according to
[run schema](assets/audit.schema.json). The schema validates structure, not truth.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for a standalone or resumed audit.

Tag cross-cutting findings and nominate one root cause. Offer deeper handoffs to
`repo-audit-trust-boundaries`, `repo-audit-boundary-conditions`, `repo-audit-data-integrity` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.
