---
name: repo-audit-agent-configs
description: "Audit repository agent configurations, skill bundles, instruction files, and MCP server definitions with code evidence and counterexamples. Use when inspecting .claude/skills, .agents/skills, .cursor/skills, AGENTS.md, .cursorrules, or .mcp.json for prompt injection, unvetted scripts, or unsafe agent tool permissions."
---

# Agent Configurations and Skill Bundles

Investigate this contract dimension deeply and return evidence-backed findings.
Use the supplied source snapshot; do not execute untrusted bundled scripts or connect to external MCP endpoints.

## Start

1. Read [shared protocol](references/protocol.md) once per run. Use its scope,
   evidence statuses, severity/confidence rubric, and application-code mutation boundary.
2. Read [investigation method](references/method.md). Inspect the listed inventory,
   host loading surfaces, and safeguards before promoting a search match to a finding.
3. Reuse the existing audit.json and contract IDs if present. If running alone,
   establish a minimal run envelope and inspect required upstream contracts yourself.
   Related skills are optional deeper investigations, not mandatory dependency chains.

## Inventory

`.claude/skills`, `.agents/skills`, `.cursor/skills`, other host skill directories (`.windsurf/skills`, `.github/skills`, `.gemini/skills`), `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules`, `.clinerules`, `.github/copilot-instructions.md`, `.mcp.json` (plus `.cursor/mcp.json` and `.vscode/mcp.json`), bundled `scripts/` executables, and checked-in agent hook or permission files.

## Investigation

1. Enumerate every repository-checked-in agent instruction file, skill manifest (`SKILL.md`, `references/*.md`, `assets/prompt.md`), bundled script (`scripts/*`), hook definition, and MCP configuration.
2. Inspect Markdown prompts and editor rules for hidden bidirectional or zero-width Unicode controls, HTML comment blocks outside fenced code, coercive instruction-override directives, and remote image exfiltration URLs.
3. Trace Python and shell scripts inside `.claude/skills`, `.agents/skills`, `.cursor/skills`, and sibling host folders for undeclared network clients, `shell=True` or `eval`/`exec` sinks, symlink escapes, and unapproved host credential or session-store reads.
4. Audit `.mcp.json` and editor MCP configs for shell-wrapped server commands, unpinned auto-installed packages (`npx -y` without a pinned version), unencrypted or untrusted remote endpoints, and literal tokens in `env` or `headers`.
5. Separate confirmed prompt-injection or code-execution vectors, statically established configuration defects, credible unverified supply-chain risks, and optional hardening. Never execute untrusted skill scripts or invoke remote MCP servers.

## Challenge and validate

Use this failure shape only as a discovery lead: A checked-in skill under `.claude/skills/` embeds a zero-width comment directive in `SKILL.md` and pairs it with a helper script or `.mcp.json` entry that exfiltrates environment tokens over HTTP.

Actively test this counterexample: A standalone offline Python helper script that parses workspace JSON using only the standard library or reads an explicitly documented local diagnostic directory without network or shell sinks is a valid local workflow, not a vulnerability.

Verify findings statically against exact file lines, Unicode code points, AST call sites, and MCP transport fields.

Check the repository's existing tooling before proposing commands. Read
[research references](references/sources.md) only when selecting a technique or
checking a host/specification boundary. No listed tool is a prerequisite.

## Deliver and hand off

Produce the topic inventory and report using [report template](assets/report-template.md).
Record findings, healthy counterexamples, and coverage in audit.json according to
[run schema](assets/audit.schema.json). The schema validates structure, not truth.
Use [handoff template](assets/handoff-template.md) for continuation and
[invocation prompt](assets/prompt.md) for a standalone or resumed audit.

Tag cross-cutting findings and nominate one root cause. Offer deeper handoffs to
`repo-audit-trust-boundaries`, `repo-audit-configuration`, `repo-audit-canonicalization` when the evidence warrants them.
Do not automatically load these skills, create remote issues, or modify application code.
Use `repo-audit-synthesis` to consolidate results into a prioritized issue plan.
