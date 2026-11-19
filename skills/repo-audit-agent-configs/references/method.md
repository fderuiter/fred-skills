# Investigation method: Agent Configurations and Skill Bundles

## Scope selection

Inventory every agent configuration surface checked into the repository before sampling individual files:
host skill directories (`.claude/skills`, `.agents/skills`, `.cursor/skills`, `.windsurf/skills`, `.github/skills`, `.gemini/skills`),
repository instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.windsurfrules`, `.clinerules`, `.github/copilot-instructions.md`),
and Model Context Protocol or tool manifests (`.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`).
Do not mark a repository clean after checking only `AGENTS.md` if editor skill directories or MCP configs exist.

## Contract worksheet

Record the expected authority boundary for each agent configuration surface:
which files are automatically loaded into agent context, which scripts or MCP servers can execute commands or network calls,
which environment variables or host directories they read, and what pinning or review controls govern updates.
If an external MCP endpoint or capability is intentional for a team workflow, record the missing pinning, transport, or documentation control as hardening or needs-validation rather than assuming compromise.

## Topic-specific discovery probes

Apply the five investigation steps in SKILL.md across every discovered instruction file, skill bundle, and MCP definition:
1. Scan raw UTF-8 code points for bidirectional controls, zero-width characters, and Unicode tag characters.
2. Parse Markdown outside fenced code blocks for HTML comment blocks, coercive role/instruction overrides, and remote Markdown or HTML image tags that leak prompt state through query parameters.
3. Parse bundled Python scripts via AST and shell scripts via command patterns to locate network clients, `shell=True`, `eval`/`exec`, symlink escapes, and undeclared credential or home-directory reads.
4. Inspect `.mcp.json` server entries for `sh -c`/`bash -c`/`cmd /c` wrappers, unpinned `npx -y` package execution, unencrypted `http://` non-loopback URLs, and literal API keys in `env` or `headers`.
5. Record both risky artifacts and verified clean controls with exact file paths and line numbers.

## Adversarial case

A checked-in skill under `.claude/skills/` embeds hidden instructions inside an HTML comment or bidirectional Unicode sequence in `SKILL.md` while bundling a `scripts/` helper or `.mcp.json` server that transmits workspace secrets to an external host.

Trace how host auto-discovery loads the instruction file or registers the MCP server, identify the exact code point or AST call site, and document the static evidence without running the untrusted payload. If no repository artifact exhibits this pattern, record that the adversarial lead was refuted.

## Safe control

A local repository skill contains Markdown instructions with fenced code examples only, uses a standard-library-only offline Python script with explicit path bounds, and defines local stdio MCP servers with pinned versions and `${ENV_VAR}` placeholders rather than hardcoded tokens.

Preserve legitimate developer automation and capability-scoped diagnostic scripts when recommending remediations.

## Independent validation

Run deterministic static checks (for example `python scripts/fred.py vet <repository>` when available, or offline AST/UTF-8 inspection) to verify line numbers, code points, and JSON keys. Never execute untrusted bundled scripts or connect to third-party MCP URLs to test exploitability.

## Cross-topic boundary

Keep repository agent configuration and skill bundle findings in this specialist pass, and hand off application HTTP/API authorization flaws to `repo-audit-trust-boundaries`, application runtime config precedence bugs to `repo-audit-configuration`, or parser encoding mismatches to `repo-audit-canonicalization`. Preserve shared evidence IDs in `audit.json`.
