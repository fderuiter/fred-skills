Use repo-audit-agent-configs to audit agent configurations, instruction files, and skill bundles in <repository> at <revision> across <directory> and <symbol>.
Prioritize <critical workflows> and reuse <existing audit workspace, if any> while checking issue <issue-number>.
Inventory `.claude/skills`, `.agents/skills`, `.cursor/skills`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.mcp.json`, and bundled `scripts/` files.
Inspect instruction files for hidden Unicode controls, HTML comments outside code fences, remote exfiltration images, and prompt override directives.
Trace bundled scripts and MCP server definitions statically for outbound network clients, shell/dynamic execution, and undeclared credential reads with file/symbol/line evidence.
Return a standalone report plus `audit.json` conforming to `assets/audit.schema.json`.
Honor <allowed-paths> and <do-not-modify-paths>; never execute untrusted skill scripts, connect to MCP servers, modify application code, or publish issues unless explicitly authorized.
