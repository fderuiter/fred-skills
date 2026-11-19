# Agent Host Integration and Orchestration Guide

This document describes how to install, configure, and orchestrate fred-skills across supported agent hosts, custom harnesses, and continuous integration pipelines.

All workflows follow the audit-only default policy defined in [AGENTS.md](../AGENTS.md). The audit suite analyzes source code as evidence. It does not modify application code or publish remote issues without explicit human authorization.

## Supported Agent Hosts

The toolkit catalogs nine agent hosts in [catalog/hosts.json](../catalog/hosts.json). The table below lists installation targets, invocation syntax, and marker files. Native plugin and extension manifests are generated at [.claude-plugin/marketplace.json](../.claude-plugin/marketplace.json), [.cursor-plugin/marketplace.json](../.cursor-plugin/marketplace.json), [.cursor-plugin/plugin.json](../.cursor-plugin/plugin.json), and [gemini-extension.json](../gemini-extension.json).

| Host | Project Path | User Path | Invocation Syntax | Marker File | Behavioral Test Status |
| --- | --- | --- | --- | --- | --- |
| OpenAI Codex (`codex`) | `.agents/skills` | `.agents/skills` | `$skill-name` | `.agents` | not-executed-by-this-host-adapter |
| Claude Code (`claude-code`) | `.claude/skills` | `.claude/skills` | `/skill-name` or natural language | `.claude` | not-executed |
| Antigravity (`antigravity`) | `.agents/skills` | `.gemini/antigravity/skills` | Natural language invocation | `.agents` | not-executed |
| Gemini CLI (`gemini-cli`) | `.gemini/skills` | `.gemini/skills` | Natural language invocation | `.gemini` | not-executed |
| Cursor (`cursor`) | `.cursor/skills` | `.cursor/skills` | `/skill-name` or natural language | `.cursor` | not-executed |
| GitHub Copilot (`copilot`) | `.github/skills` | `.copilot/skills` | `/skill-name` or natural language | `.github/skills` (primary), `.github` (fallback) | not-executed |
| OpenCode (`opencode`) | `.opencode/skills` | `.config/opencode/skills` | `/skill-name` or natural language | `.opencode` (project), `.config/opencode` (user) | not-executed |
| Windsurf (`windsurf`) | `.windsurf/skills` | `.config/devin/skills` | `@skill-name` or natural language | `.windsurf`, `.devin` (project), `.config/devin` (user) | not-executed |
| Kiro (`kiro`) | `.kiro/skills` | `.kiro/skills` | `/skill-name` or natural language | `.kiro` | not-executed |

> Note: Host adapter filesystem checks verify directory layout. They do not certify host runtime behavior or model performance.

---

## Host Integration Guides

### OpenAI Codex

OpenAI Codex discovers skills stored in the `.agents/skills` directory.

#### Installation Paths
- **Project Scope**: `<project-root>/.agents/skills/<skill-name>/`
- **User Scope**: `<user-home>/.agents/skills/<skill-name>/`

#### Installation Commands
Install skills into `.agents/skills` using OpenAI Codex's built-in `$skill-installer` prompt or the local toolkit CLI:

1. **Codex `$skill-installer` prompt**:
   ```text
   $skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-concurrency
   # Or install another specialist into .agents/skills:
   $skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-trust-boundaries
   ```

2. **Toolkit CLI (Project Scope)**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host codex --pack starter
   ```

3. **Toolkit CLI (User Scope)**:
   ```sh
   python scripts/fred.py install --destination "$HOME" --host codex --scope user --pack starter
   ```

4. **Install individual skills**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host codex --skill repo-audit-concurrency --skill repo-audit-trust-boundaries
   ```

#### Invocation Syntax
Activate a skill in Codex with the dollar sign prefix:
```text
$repo-audit-concurrency audit background worker loops in src/worker.py
```

#### Verification
Verify the skill files on the filesystem:
```sh
ls -la /path/to/project/.agents/skills/repo-audit-concurrency/SKILL.md
```

---

### Claude Code

Claude Code discovers skills stored in the `.claude/skills` directory.

#### Installation Paths
- **Project Scope**: `<project-root>/.claude/skills/<skill-name>/`
- **User Scope**: `<user-home>/.claude/skills/<skill-name>/`

#### Installation Commands
Register the marketplace catalog and install the 6-skill `starter` pack (`<= 1,500` raw characters), another curated pack, an individual skill, or the full suite plugin, or use the local toolkit CLI:

1. **Marketplace install (`starter` pack, individual skill, or full suite)**:
   ```sh
   /plugin marketplace add fderuiter/fred-skills
   /plugin install starter@fred-skills
   # Or install one specialist:
   /plugin install repo-audit-concurrency@fred-skills
   # Full 57-skill bundles (exceed Stage-1 discovery budgets on smaller context windows):
   /plugin install complete@fred-skills
   /plugin install fred-skills@fred-skills
   ```

   > **Stage-1 context budget warning:** Installing `complete@fred-skills` or `fred-skills@fred-skills` loads all 57 skills at startup, which can crowd or exceed Stage-1 skill-listing budgets in [Claude Code](https://code.claude.com/docs/en/skills) (`1%` of context, `skillListingBudgetFraction = 0.01`) and [OpenAI Codex](https://developers.openai.com/codex/skills) (`2%` of context or `8,000`-character fallback). Prefer `starter@fred-skills` (`6` skills) or focused packs by default.

2. **Toolkit CLI (Project Scope)**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host claude-code --pack starter
   ```

3. **Toolkit CLI (User Scope)**:
   ```sh
   python scripts/fred.py install --destination "$HOME" --host claude-code --scope user --pack starter
   ```

4. **Install individual skills**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host claude-code --skill repo-audit-concurrency
   ```

#### Invocation Syntax
Activate a skill using slash syntax or natural language:
```text
/repo-audit-concurrency Trace concurrency hazards and cancellation boundaries in src/jobs/
```

#### Verification
Verify the skill files on the filesystem:
```sh
ls -la /path/to/project/.claude/skills/repo-audit-concurrency/SKILL.md
```

---

### Cursor

Cursor discovers skills in `.cursor/skills` and loads Cursor Plugins from `.cursor-plugin/plugin.json` and `.cursor-plugin/marketplace.json`.

#### Installation Paths
- **Project Scope**: `<project-root>/.cursor/skills/<skill-name>/`
- **User Scope**: `<user-home>/.cursor/skills/<skill-name>/`

#### Installation Commands
Install curated packs (`starter` by default) and skills locally with the toolkit CLI, or import `https://github.com/fderuiter/fred-skills` as a Git repository marketplace from **Dashboard -> Plugins & MCPs** (**Import from Repo**) and install an individual specialist (recommended) or `fred-skills` (loads all 57 skills; see Stage-1 context budget note above) from **Customize** in the sidebar:

1. **Toolkit CLI (Project Scope)**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host cursor --pack starter
   ```

2. **Toolkit CLI (User Scope)**:
   ```sh
   python scripts/fred.py install --destination "$HOME" --host cursor --scope user --pack starter
   ```

3. **Install individual skills**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host cursor --skill repo-audit-concurrency
   ```

#### Invocation Syntax
Activate a skill from **Customize** or invoke it with slash syntax or natural language in chat:
```text
/repo-audit-concurrency Trace concurrency hazards and cancellation boundaries in src/jobs/
```

#### Verification
Verify the skill files on the filesystem:
```sh
ls -la /path/to/project/.cursor/skills/repo-audit-concurrency/SKILL.md
```

---

### Antigravity

Antigravity uses the portable skills standard. It uses `.agents/skills` for project scope and `.gemini/antigravity/skills` for user scope.

#### Installation Paths
- **Project Scope**: `<project-root>/.agents/skills/<skill-name>/`
- **User Scope**: `<user-home>/.gemini/antigravity/skills/<skill-name>/`

#### Installation Commands
Antigravity shares the project marker `.agents` with OpenAI Codex. For project installations, specify `--host antigravity` explicitly to avoid host ambiguity:

1. **Toolkit CLI (Project Scope)**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host antigravity --pack starter
   ```

2. **Toolkit CLI (User Scope)**:
   ```sh
   python scripts/fred.py install --destination "$HOME" --host antigravity --scope user --pack starter
   ```

3. **Install individual skills**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host antigravity --skill repo-audit-state-machines
   ```

#### Invocation Syntax
Invoke the skill directly in your session prompt:
```text
Use repo-audit-state-machines to audit session lifecycle transitions in auth/session.py.
```

#### Verification
1. Verify the skill files at the target path:
   ```sh
   # Project scope
   ls -la /path/to/project/.agents/skills/repo-audit-state-machines/SKILL.md

   # User scope
   ls -la "$HOME/.gemini/antigravity/skills/repo-audit-state-machines/SKILL.md"
   ```
2. Verify the installation receipt:
   ```sh
   cat /path/to/project/.fred-skills/antigravity-project/receipt.json
   ```

---

### Gemini CLI

Gemini CLI discovers skills in the `.gemini/skills` directory.

#### Installation Paths
- **Project Scope**: `<project-root>/.gemini/skills/<skill-name>/`
- **User Scope**: `<user-home>/.gemini/skills/<skill-name>/`

#### Installation Commands
1. **Toolkit CLI (Project Scope)**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host gemini-cli --pack starter
   ```

2. **Toolkit CLI (User Scope)**:
   ```sh
   python scripts/fred.py install --destination "$HOME" --host gemini-cli --scope user --pack starter
   ```

3. **Install individual skills**:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host gemini-cli --skill repo-audit-behavioral-contracts
   ```

> **Stage-1 context budget note (`gemini-extension.json`):** Installing the repository-wide Gemini CLI extension (`gemini extensions install https://github.com/fderuiter/fred-skills`) exposes all 57 skills at startup. Prefer `--pack starter` (`6` skills, `<= 1,500` raw characters) when sharing workspaces across hosts subject to [OpenAI Codex](https://developers.openai.com/codex/skills) and [Claude Code](https://code.claude.com/docs/en/skills) Stage-1 discovery budgets.

#### Invocation Syntax
Activate skills by prompt reference:
```text
Use repo-audit-behavioral-contracts to inspect API request and response validation in api/v1/routes.py.
```

#### Verification
1. Check the installed skill files:
   ```sh
   ls -la /path/to/project/.gemini/skills/repo-audit-behavioral-contracts/SKILL.md
   ```
2. Check the installation receipt:
   ```sh
   python scripts/fred.py installed --destination /path/to/project --host gemini-cli
   ```

---

## Host Discovery Mechanics and Marker Files

The function `detect_host()` in `scripts/fredlib/installation.py` identifies target hosts automatically from declarative `project_markers` and `user_markers` rules in `catalog/hosts.json`.

### Marker Resolution Rules
1. **Tiered Marker Detection**:
   - **Primary project markers**: `.claude` (`claude-code`), `.cursor` (`cursor`), `.gemini` (`gemini-cli`), `.opencode` (`opencode`), `.windsurf` or `.devin` (`windsurf`), `.kiro` (`kiro`), `.github/skills` (`copilot`), and `.agents` (`codex` by default, or `antigravity` when an `antigravity` receipt exists).
   - **Fallback project marker**: Bare `.github` resolves to `copilot` only when no primary marker or conflicting host receipt exists.
   - **User-scope markers** (`--scope user`): `.claude`, `.cursor`, `.agents`, `.gemini`, `.copilot`, `.config/opencode`, `.config/devin`, and `.kiro`.

2. **Multi-Host Fan-Out vs. Single-Host Safeguards**:
   - When `--host` is omitted, `install` defaults to the `starter` pack, fans out atomically across all detected markers (deduplicating shared `.agents/skills` targets), or bootstraps `.agents/skills` under `codex` when zero markers exist; `update` and `installed` also aggregate across detected hosts.
   - Direct `detect_host()` calls and destructive commands (`remove`, `rollback`, `recover-install`) raise `ValueError` requiring `--host` when zero markers, multiple host markers, or multiple host receipts exist.
   - If conflicting receipts exist for a shared target path (such as both `codex` and `antigravity` project receipts), resolution raises `ValueError`.

3. **Explicit Override**:
   Always pass `--host <name>` in automated scripts to avoid ambiguity.

### Atomic Operation Safeguards
The installer manages state under `<destination>/.fred-skills/<host>-<scope>/`:
- **Exclusive Lock (`operation.lock`)**: Blocks concurrent install operations.
- **Atomic Journal (`operation.json`)**: Tracks install progress phases (`prepared`, `committed`, `recovered`).
- **Cryptographic Receipts (`receipt.json`)**: Records SHA-256 hashes of all managed files.
- **Rollback and Recovery**:
   ```sh
   # Roll back the latest install or remove operation
   python scripts/fred.py rollback --destination /path/to/project --host <host>

   # Recover from an interrupted install operation
   python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted
   ```

---

## Multi-Agent Orchestration Patterns

Multi-agent orchestration coordinates specialized agents while maintaining strict trust boundaries.

### Orchestration Architecture and Specialist Delegation

An audit workflow separates responsibilities into three agent roles:

```
                  +-----------------------------+
                  |      Coordinator Agent      |
                  |   (repo-audit-program)      |
                  +--------------+--------------+
                                 |
        +------------------------+------------------------+
        |                        |                        |
        v                        v                        v
+---------------+        +---------------+        +---------------+
|  Specialist 1 |        |  Specialist 2 |        |  Specialist N |
| (Concurrency) |        | (Trust Bounds)|        | (State Machine|
+-------+-------+        +-------+-------+        +-------+-------+
        |                        |                        |
        +------------------------+------------------------+
                                 |
                                 v
                  +-----------------------------+
                  |       Synthesis Agent       |
                  |   (repo-audit-synthesis)    |
                  +-----------------------------+
```

1. **Coordinator Agent (`repo-audit-program`)**:
   - Initializes the audit run and plans execution slices.
   - Divides target repositories into bounded areas.
   - Preserves source snapshot state across phases.
   - Manages execution checkpoints (`running`, `paused`, `completed`).

2. **Specialist Agents (`repo-audit-*`)**:
   - Perform focused investigations in specific domains (for example, concurrency, trust boundaries, state machines).
   - Reconstruct prerequisites when executed in isolation.
   - Collect concrete evidence, code references, and counter-examples.
   - Record candidate findings in the audit ledger without changing application code.

3. **Synthesis Agent (`repo-audit-synthesis`)**:
   - Challenges candidate findings and eliminates false positives.
   - Merges duplicate observations across specialists.
   - Generates local issue drafts and implementation workstreams.
   - Requires explicit human authorization before remote issue publication.

### Trust Boundaries and Guardrails
- **Read-Only Codebase Access**: All agents operate in audit-only mode. Agents do not modify application code during audits.
- **Draft-Only Finding Output**: Findings remain local records. Agents do not publish remote issues without human review.
- **Structured Communication**: Agents communicate only through validated schema files.

### Artifact Handoff Protocol
Agents exchange information through structured artifacts (`fred.py` exits `1` on runtime/schema errors, `2` on invalid CLI flags):

| Artifact | Schema Contract | Purpose in Multi-Agent Handoff |
| --- | --- | --- |
| `run.json` | `schemas/run.schema.json` | Records run ID, Git commit hash, file inventory hashes, selected skills, and checkpoints. |
| `audit.json` | Existing schema 1.0 | Canonical audit ledger holding evidence, findings, checks, coverage, and contracts. |
| `prompt.md` | Markdown contract | Self-contained instructions for downstream agents. |
| `revalidation.json` | `resume` / `revalidate` JSON | Records file changes between snapshots (`resume`) to flag stale findings. |

### End-to-End Multi-Agent Workflow Example

```sh
# Step 1: Coordinator initializes the audit run
python scripts/fred.py init /path/to/target-repo \
  --task investigate-bug \
  --budget brief \
  --output ./audit-workspace

# Step 2: Coordinator marks run state as running
python scripts/fred.py checkpoint ./audit-workspace \
  --state running \
  --note "Starting multi-agent audit pass"

# Step 3: Validate the workspace before handoff
python scripts/fred.py validate-run ./audit-workspace

# Step 4: Specialist agents execute audits using prompt.md instructions
# Specialist results append findings into ./audit-workspace/audit.json

# Step 5: Coordinator records progress checkpoint
python scripts/fred.py checkpoint ./audit-workspace \
  --state running \
  --note "Specialist passes complete; beginning synthesis"

# Step 6: Synthesis agent validates and creates local drafts
python scripts/fred.py issues ./audit-workspace/audit.json \
  --output ./audit-workspace/issue-drafts \
  --format markdown

# Step 7: Coordinator marks the run completed
python scripts/fred.py checkpoint ./audit-workspace \
  --state completed \
  --note "Audit run complete; drafts ready for human review"
```

---

## Continuous Integration Automation

You can run automated audit checks in continuous integration pipelines with strict audit-only safeguards.

### Audit Safeguards for CI
1. Set repository permissions to read-only (`contents: read`).
2. Do not supply write tokens or issue creation permissions.
3. Save audit ledgers and draft reports as workflow artifacts.
4. Fail the workflow if ledger schema validation fails (`validate-run` exits `1`; invalid CLI flags exit `2`).

### Complete GitHub Actions Workflow Example

Create `.github/workflows/audit-safeguard.yml` in your target repository:

```yaml
name: Security and Architecture Audit

on:
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  audit:
    name: Run Headless Audit Checks
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install fred-skills dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pyyaml jsonschema

      - name: Run audit preflight inspection
        run: |
          python scripts/fred.py preflight . --full

      - name: Plan change review audit
        run: |
          python scripts/fred.py plan . \
            --task review-pr \
            --mode changes \
            --base origin/main \
            --json > audit-plan.json

      - name: Validate audit plan schema
        run: |
          python -c "import json, sys; data=json.load(open('audit-plan.json')); assert 'skills' in data['plan']"

      - name: Generate local draft issues
        run: |
          mkdir -p audit-drafts
          # Generate issue drafts for inspection without remote publishing
          if [ -f audit.json ]; then
            python scripts/fred.py issues audit.json --output audit-drafts --format markdown
          fi

      - name: Upload audit artifacts
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: audit-results
          path: |
            audit-plan.json
            audit-drafts/
          retention-days: 14
```

---

## Manual Harness Integration for Unsupported Hosts

Custom agent harnesses can integrate skills directly from the filesystem.

### Standard Filesystem Structure
Every skill conforms to the portable Agent Skills layout:

```text
skills/<skill-name>/
├── SKILL.md                 # Entry point with YAML frontmatter
├── agents/                  # Optional host-specific UI metadata
│   └── openai.yaml
├── assets/                  # Prompts and templates
│   └── prompt.md
└── references/              # Context documents and methodologies
    ├── methodology.md
    └── protocol.md
```

### Skill Entry Point Requirements
`SKILL.md` contains YAML frontmatter and operational instructions:
- `name`: Must match the directory name exactly (lowercase letters, digits, and hyphens).
- `description`: 1 to 1024 characters describing purpose and trigger conditions.
- Body: Markdown instructions under 500 lines.
- References: All relative links must resolve inside the skill directory. Links must not escape to parent directories.

### Manual Harness Integration Pattern
To inject a skill into a custom agent harness:

1. **Unpack or copy the skill package**:
   ```sh
   mkdir -p /path/to/harness/skills/
   tar -xzf repo-audit-concurrency.tar.gz -C /path/to/harness/skills/
   ```

2. **Load skill instructions into context**:
   Read `skills/<skill-name>/SKILL.md` and append it to the agent system prompt:
   ```python
   from pathlib import Path


   def load_skill_prompt(skill_dir: Path) -> str:
       skill_file = skill_dir / "SKILL.md"
       content = skill_file.read_text(encoding="utf-8")
       # Extract markdown body below frontmatter
       parts = content.split("---", 2)
       body = parts[2] if len(parts) >= 3 else content
       return body.strip()
   ```

3. **Enforce Audit Schema 1.0 Output**:
   Instruct the agent to produce structured findings conforming to the audit schema:
   ```json
   {
     "schema_version": "1.0",
     "run_id": "harness-run-001",
     "repository": {
       "identity": "example-service",
       "revision": "main",
       "dirty": false,
       "snapshot": "sha256:example"
     },
     "scope": ["src/queue.py"],
     "coverage": [
       {
         "topic": "concurrency",
         "scope": "src/queue.py",
         "status": "inspected",
         "evidence_ids": ["E-001"],
         "notes": "Reviewed worker dispatch loop and task spawning bounds.",
         "next_action": "Add bounded semaphore regression test."
       }
     ],
     "contracts": [
       {
         "id": "C-001",
         "dimension": "concurrency",
         "scope": "src/queue.py",
         "intended": "Worker bounds concurrent task execution",
         "observed": "Worker spawns unbounded background tasks",
         "enforcement": "none",
         "evidence_ids": ["E-001"]
       }
     ],
     "evidence": [
       {
         "id": "E-001",
         "path": "src/queue.py",
         "line_start": 42,
         "line_end": 58,
         "symbol": "QueueWorker.dispatch",
         "context": "Worker spawns tasks without a concurrency semaphore."
       }
     ],
     "findings": [
       {
         "id": "F-001",
         "topic": "concurrency",
         "tags": ["backpressure", "worker"],
         "title": "Unbounded concurrency in queue worker",
         "status": "supported",
         "severity": "high",
         "confidence": "high",
         "contract_ids": ["C-001"],
         "evidence_ids": ["E-001"],
         "preconditions": "Burst of incoming queue messages exceeds worker capacity",
         "sequence": [
           "Producer enqueues a large batch of jobs",
           "QueueWorker.dispatch spawns a task per message without awaiting a semaphore"
         ],
         "expected": "In-flight worker tasks remain bounded by configured concurrency",
         "actual": "Unbounded tasks exhaust memory and downstream connection pools",
         "impact": "Worker process exhaustion under burst traffic",
         "root_cause": "Missing concurrency semaphore around task creation",
         "counterevidence": "No upstream rate limiter caps burst delivery to QueueWorker",
         "remedy": "Guard task spawning with an explicit asyncio.Semaphore limit",
         "acceptance": [
           "Concurrent in-flight tasks never exceed the configured limit"
         ],
         "validation": {
           "state": "not-run",
           "commands": [],
           "result": "Static inspection only; runtime load test not executed."
         },
         "related_findings": []
       }
     ],
     "issues": [],
     "limitations": [
       "Static harness example; runtime load test not executed."
     ],
     "checks": []
   }
   ```
