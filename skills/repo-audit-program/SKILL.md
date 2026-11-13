---
name: repo-audit-program
description: "Coordinate comprehensive repository audits for hidden behavioral contracts. Use when a user requests a multi-topic architecture/correctness audit or this three-pass audit program."
---

# Repository Audit Program

Coordinate deep workflow-based audits and preserve evidence between passes.

## Establish the run

Read [shared protocol](references/protocol.md), then [catalog](references/catalog.md).
Resolve repository/revision, current changes, critical workflows, architecture/runtime,
available source/tools, existing audit artifacts, and applicable project instructions.
Keep application code unchanged unless the user's request authorizes corrections.
Read [repository profiles](references/profiles.md) only for prioritization; profiles are
hypotheses to verify, not findings or claims about the current code.

Create an audit workspace containing audit.json, a scope/coverage ledger, topic reports,
and continuation handoffs. Use [schema](assets/audit.schema.json),
[report template](assets/report-template.md), and [handoff template](assets/handoff-template.md).
Record requested, reviewed, deferred, excluded, and not-applicable topics explicitly.

## Select depth and sequence

Default a broad request to comprehensive coverage of all applicable catalog topics.
For a narrow request select only relevant topics, state selection, and preserve remaining
scope. Complete critical workflow slices; use checkpoints when the full scope cannot
fit one context. Never claim all behavior is audited because all files were searched.

1. **Pass 1 — Behavioral discovery:** Start with behavioral-contracts. Reconstruct
   invariants, state machines, concurrency, lifecycle, temporal coupling, and domain models.
   Record independently supported intended contracts and observed enforcement separately.
2. **Pass 2 — Correctness and resilience:** Use those contracts for failure modes,
   trust, transactions, idempotency, error semantics, capacity, cancellation, cache coherence,
   data integrity, boundaries, protocols, configuration, compatibility, complexity, and
   canonicalization. Escalate a reachable serious security defect immediately without
   waiting for a pass to finish. Preliminary trust triage may happen at run setup.
3. **Pass 3 — Architecture and maintainability:** Examine side effects, sources of
   truth, architecture, test contracts, cohesion, observability, nondeterminism, test oracles,
   API consistency, accessibility state, and dead code. Link mechanisms to earlier findings.
4. Run repo-audit-synthesis to consolidate and challenge root causes, check existing
   issue coverage when accessible, and produce implementation workstreams and issue drafts.

Use catalog names to resolve installed skills; if unavailable, report that limitation and
perform the method from accessible resources rather than assuming a specific tool exists.
Load only the current specialist and required references. A specialist can run standalone
by reconstructing its own prerequisites; this sequence is an evidence-reuse strategy.

## Coordinate and resume

Run sequentially by default. If the user authorizes delegation and the runtime supports it,
partition by independent subsystems or bounded specialist tasks, allocate ID ranges, and
merge returned artifacts centrally. Give raw source plus contract/evidence artifacts, not
expected findings. Do not let workers write the canonical ledger concurrently.

On resume compare source snapshots, revalidate changed evidence and dependent contracts,
then continue from recorded scope. Preserve healthy patterns and refuted candidates.
Use one root-cause finding with cross-topic tags where appropriate; do not collapse
independently fixable defects into a vague architectural epic.

## Validate and deliver

Run `python3 <this-skill>/scripts/validate_audit.py <audit.json>` after producing the ledger.
The checker needs Python 3 and jsonschema; use an approved existing environment. If absent,
report the blocked check and review against the schema manually. This validates shape,
references, and issue-dependency cycles, not evidence truth or audit completeness.

Deliver an executive account of material risks; coverage and limitations; contract and
pattern inventory; prioritized findings with counterevidence; actual validation results;
an acyclic implementation order; issue drafts; and a precise next-action handoff.
An empty finding list is valid when warranted. Do not invent severity or findings to fill
every topic. Use [master prompt](assets/prompt.md) for a new or resumed run.
Read [market/tool research](references/market.md) when choosing existing analysis aids.
