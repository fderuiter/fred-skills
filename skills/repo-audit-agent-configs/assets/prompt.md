Use repo-audit-agent-configs to conduct an agent configuration and skill bundle audit of <repository> at <revision>.

Prioritize <critical workflows> and inspect `.claude/skills`, `.agents/skills`, `.cursor/skills`, `AGENTS.md`, `.cursorrules`, and `.mcp.json` in bounded slices.
Reuse <existing audit workspace, if any>. Establish host trust boundaries independently,
inspect prompts and rules for hidden Unicode, HTML comments, remote images, and directive overrides,
and statically trace bundled `scripts/` and MCP definitions for network, shell, or credential risks.
Separate confirmed defects, statically supported findings, unresolved risks, hardening, and refuted candidates.
Report file/symbol/line evidence tied to the audited snapshot. Record honest coverage, checks run,
validation not run, severity and confidence, minimal remedies, acceptance criteria, and handoffs.
Keep IDs stable and preserve unrelated records. Return a standalone report plus audit.json conforming to assets/audit.schema.json.
Do not execute untrusted skill scripts, connect to MCP servers, modify application code, or publish issues unless separately authorized.
