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

Register the marketplace catalog and install `starter` for 6 core audit skills (`<= 1,500` raw characters), another curated pack, an individual skill, or `complete` for all 57 packages / the `fred-skills` suite plugin, use `npx skills add`, install a native extension, or install via the local CLI (see the [integration guide](integration.md) for OpenAI Codex, Claude Code, Cursor, GitHub Copilot, Antigravity, Gemini CLI, OpenCode, Windsurf, and Kiro). **Stage-1 context budget warning:** Prefer `starter@fred-skills` (`6` skills, `<= 1,500` raw characters) or focused domain packs by default; installing `complete@fred-skills`, `fred-skills@fred-skills`, `fred-skills-suite.tar.gz`, or `gemini-extension.json` exposes all 57 skills at startup, which can crowd or exceed Stage-1 discovery budgets on smaller context windows in [OpenAI Codex](https://developers.openai.com/codex/skills) (`2%` of context or `8,000`-character fallback) and [Claude Code](https://code.claude.com/docs/en/skills) (`1%` of context, `skillListingBudgetFraction = 0.01`).

### Claude Code

```sh
/plugin marketplace add fderuiter/fred-skills
/plugin install starter@fred-skills
/plugin install repo-audit-concurrency@fred-skills
/plugin install complete@fred-skills
/plugin install fred-skills@fred-skills
```

### Cursor

Install the recommended 6-skill `starter` pack into `.cursor/skills/` locally with the toolkit CLI below, or import `https://github.com/fderuiter/fred-skills` as a Git repository marketplace through **Dashboard -> Plugins & MCPs** (**Import from Repo**) and open **Customize** in the sidebar to install an individual specialist (recommended) or `fred-skills` (loads all 57 skills; see Stage-1 budget warning above) at project or user scope:

```sh
python scripts/fred.py install --destination /path/to/project --host cursor --pack starter
```

### Skills CLI

Install skills across supported agent hosts with the open [`skills`](https://skills.sh/docs) CLI:

```sh
npx skills add fderuiter/fred-skills --skill repo-audit-concurrency
# Or add the full 57-skill repository (see Stage-1 budget warning above):
npx skills add fderuiter/fred-skills
```

### Codex

Install skills into `.agents/skills/` using the local CLI (`starter` pack by default) or OpenAI Codex's built-in `$skill-installer`:

```sh
python scripts/fred.py install --destination /path/to/project --host codex --pack starter
```

```text
$skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-concurrency
```

### Gemini CLI Extension

Prefer installing the 6-skill `starter` pack via the toolkit CLI (`python scripts/fred.py install --destination /path/to/project --host gemini-cli --pack starter`) for bounded Stage-1 discovery. Installing the repository-wide Gemini CLI extension (`gemini-extension.json`) loads all 57 skills at startup:

```sh
gemini extensions install https://github.com/fderuiter/fred-skills
```

### Toolkit CLI (Project and User Scopes)

Run `install` without flags to install the `starter` pack across all detected workspace hosts (`.claude`, `.cursor`, `.agents`, `.gemini`, `.github`, `.opencode`, `.windsurf`, `.devin`, or `.kiro`)—or bootstrap `.agents/skills/` when no markers exist—or pass `--host` explicitly:

```sh
python scripts/fred.py install

python scripts/fred.py install --destination /path/to/project --host cursor --pack starter
python scripts/fred.py install --destination /path/to/project --host copilot --pack starter
python scripts/fred.py install --destination /path/to/project --host opencode --pack starter
python scripts/fred.py install --destination /path/to/project --host windsurf --pack starter
python scripts/fred.py install --destination /path/to/project --host kiro --pack starter
python scripts/fred.py install --destination /path/to/project --host antigravity --pack starter
python scripts/fred.py install --destination /path/to/project --host gemini-cli --pack starter
```

For offline or manual workflows, copy skill directories directly to your host's skills path or use `python scripts/fred.py install` with `--scope user`. See the [integration guide](integration.md) for detailed host adapter setup, marker detection rules, and multi-agent orchestration.

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

This optional checker uses only the Python 3.12 standard library. It verifies structure, reference
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

Official releases publish versioned pack archives (led by the 6-skill `starter` pack, `full-audit`, or the complete 57-package pack) in uncompressed TAR and ZIP formats, accompanied by a `manifest.json` and `SHA256SUMS` checksums file. Prefer `starter` (`<= 1,500` raw characters) for default deployments; extracting the complete 57-package pack or `fred-skills-suite.tar.gz` loads all 57 skills and can crowd Stage-1 context budgets in [OpenAI Codex](https://developers.openai.com/codex/skills) and [Claude Code](https://code.claude.com/docs/en/skills).

### Download

Fetch release pack artifacts directly from GitHub Releases or via `curl`:

```sh
# Download a versioned pack archive and checksum manifest
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.3.0/fred-skills-0.3.0-starter.tar
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.3.0/SHA256SUMS
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.3.0/manifest.json

# Or download the complete 57-package pack
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.3.0/fred-skills-0.3.0-complete.tar
```

### Verify checksums and manifest

Check artifact integrity against the release manifest or checksums:

```sh
# Verify SHA-256 checksum against manifest
sha256sum --ignore-missing -c SHA256SUMS

# Or verify release archives using the fred toolkit
python scripts/fred.py verify-release manifest.json
```

### Extract and deploy

Unpack verified archives into your agent host skills destination:

```sh
mkdir -p /path/to/project/.agents/skills/

# Deploy starter pack
tar -xf fred-skills-0.3.0-starter.tar -C /path/to/project/.agents/skills/

# Deploy complete pack (creates all 57 skill directories)
tar -xf fred-skills-0.3.0-complete.tar -C /path/to/project/.agents/skills/
```

