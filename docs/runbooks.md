# Operational Runbooks and Troubleshooting Guide

This guide provides operational troubleshooting runbooks and disaster recovery procedures for `fred-skills` installation, state management, and planning workflows. It covers lock contention, interrupted installations, power-loss events, corrupted receipts, and Git revision resolution failures.

---

## Architecture of Installation State

The toolkit manages installation lifecycle state locally on disk without external network services or background daemons. State is isolated by target host and scope under the project root or user home directory:

```
<destination>/
  ├── <host-skills-target>/          # Installed skill packages (e.g., .claude/skills or .agents/skills)
  │     └── <skill-name>/
  │           └── SKILL.md
  └── .fred-skills/
        └── <host>-<scope>/          # State directory (e.g., claude-code-project)
              ├── operation.lock     # Exclusive file lock during active operations
              ├── operation.json     # Operation journal for two-phase commits
              ├── receipt.json       # Record of installed skills, file hashes, and history
              ├── staging/           # Temporary staging directory during package copy
              │     └── <operation-id>/
              └── backups/           # Snapshots of previous versions for rollback/recovery
                    └── <operation-id>/
```

### Core Operational Invariants

1. **Never delete receipt or backup folders to silence errors.** The receipt and backup directories contain cryptographic hashes and historical copies needed for rollback, safe uninstallation, and state verification. Deleting them leaves installed files unmanaged and creates irrecoverable drift.
2. **Inspect holding processes before removing locks.** A lock file indicates an active operation or an interrupted process. Removing a lock while an operation is actively writing will corrupt skill files.
3. **All operations verify content integrity.** Skills cannot be overwritten or removed if local files have been modified outside the toolkit, preventing accidental loss of user edits.

---

## Runbook 1: Lock Contention and Stale Operation Lock Recovery

### Context and Cause

The installation subsystem acquires an exclusive lock (`operation.lock`) via `os.open` with `O_CREAT | O_EXCL` flags in `scripts/fredlib/installation.py:L107-L121` during `install`, `update`, `remove`, and `rollback` operations. If a previous invocation was killed, crashed, or disconnected, the lock file remains on disk, blocking subsequent operations.

### Symptoms

- Any CLI installation command (`install`, `update`, `remove`, `rollback`) fails immediately.
- Exit code: `1`
- Error message on `stderr`:
  ```text
  fred-skills: Another install operation or interrupted operation holds the lock; inspect operation.json before recovery
  ```
- File exists on disk: `<destination>/.fred-skills/<host>-<scope>/operation.lock`

### Diagnostic Checks

1. **Check for active processes:**
   Verify whether another `fred.py` process is currently running on the system:
   ```sh
   pgrep -fl "fred.py" || ps aux | grep "[f]red.py"
   ```
   If a process is actively running, observe its output and wait for it to complete.

2. **Locate the specific state directory:**
   Identify the affected destination, host, and scope:
   ```sh
   # Project scope (default):
   ls -la /path/to/project/.fred-skills/

   # User scope:
   ls -la ~/.fred-skills/
   ```

3. **Inspect the operation journal (`operation.json`):**
   Examine the state of the interrupted operation:
   ```sh
   cat /path/to/project/.fred-skills/<host>-<scope>/operation.json
   ```
   Check the `phase` field in the JSON output:
   - `"phase": "prepared"`: The operation was interrupted during staging or filesystem swap. Automated recovery is required.
   - `"phase": "committed"`: The filesystem changes and receipt were committed, but the lock file was not unlinked prior to termination.
   - `"phase": "recovered"`: A previous recovery completed, but the lock file lingered.
   - File does not exist or is empty: The operation aborted before journal initialization.

4. **Compare operation ID with `receipt.json`:**
   Check if the journal's `id` already appears in `receipt.json` under `history`:
   ```sh
   python -c "
   import json, pathlib
   state = pathlib.Path('/path/to/project/.fred-skills/<host>-<scope>')
   journal = json.loads((state / 'operation.json').read_text()) if (state / 'operation.json').exists() else {}
   receipt = json.loads((state / 'receipt.json').read_text()) if (state / 'receipt.json').exists() else {'history': []}
   op_id = journal.get('id')
   committed = any(item.get('id') == op_id for item in receipt.get('history', []))
   print(f'Operation ID: {op_id}')
   print(f'Committed in receipt history: {committed}')
   "
   ```

### Step-by-Step Remediation

#### Scenario A: Interrupted Operation in "prepared" Phase
When `operation.json` indicates `phase: "prepared"` and the previous writer has stopped:
1. Execute the automated recovery command with `--confirm-interrupted`:
   ```sh
   python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted
   ```
   *(Add `--scope user` if recovering a user-scoped installation).*
2. The recovery routine will:
   - Verify that target skills and backups match expected content hashes.
   - Move any partially installed artifacts to a safe preservation directory (`backups/recovered-<uuid>/`).
   - Restore skills to their pre-operation state from `backups/<operation-id>/`.
   - Update `operation.json` phase to `"recovered"`.
   - Automatically delete `operation.lock`.

#### Scenario B: Already-Committed Operation with Lingering Lock
When `operation.json` indicates `phase: "committed"` or the operation ID is recorded in `receipt.json`:
1. Run the recovery command:
   ```sh
   python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted
   ```
2. The command verifies that all skills match their committed state and cleanly unlinks `operation.lock`, returning:
   ```json
   {
     "recovery": "already-committed",
     "skills": [...]
   }
   ```

#### Scenario C: Stale Lock with No Pending Operations
If no `fred.py` process is running, `operation.json` does not exist (or was cleaned up), and `receipt.json` is consistent:
1. Verify no process holds an open file descriptor on the lock:
   ```sh
   lsof /path/to/project/.fred-skills/<host>-<scope>/operation.lock 2>/dev/null || true
   ```
2. Safely remove only the stale lock file:
   ```sh
   rm /path/to/project/.fred-skills/<host>-<scope>/operation.lock
   ```

### Verification Steps

1. Confirm the lock file is removed:
   ```sh
   test ! -f /path/to/project/.fred-skills/<host>-<scope>/operation.lock && echo "Lock successfully cleared"
   ```
2. Run a non-mutating preview check to confirm lock acquisition and state health:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host <host> --pack starter --dry-run
   ```
   The command should succeed and output the preview JSON payload without raising a lock error.

---

## Runbook 2: Interrupted Installation and Power-Loss Recovery

### Context and Cause

An installation or update operation consists of staging files, creating a backup of pre-existing skills, atomically recording a journal (`operation.json`), replacing target directories, committing `receipt.json`, and finally updating the journal phase to `committed`. If power loss, SIGKILL, or hardware interruption occurs during this window (`scripts/fredlib/installation.py:L381-L459`), the destination skills directory may be in a partially swapped state.

Similarly, `remove` and `rollback` operations (`scripts/fredlib/installation.py:L269-L380`) move skills between the target directory and `backups/`. A power loss during these operations can result in skills residing in `backups/` while `receipt.json` remains unupdated.

### Symptoms

- Skill packages are partially copied or missing from the target directory (`.claude/skills`, `.agents/skills`, etc.).
- Subsequent operations fail with one of the following errors:
  - `fred-skills: Another install operation or interrupted operation holds the lock; inspect operation.json before recovery`
  - `fred-skills: Journal/receipt disagreement needs manual inspection; no recovery changes made`
  - `fred-skills: Modified recovery destination: <name>; no recovery changes made`
  - `fred-skills: Required recovery backup is missing or modified: <name>`
  - `fred-skills: Managed skill is missing: <name>; inspect the receipt before proceeding`

### Diagnostic Checks

1. **Verify writer process is terminated:**
   Ensure no process is modifying the directory before inspecting files:
   ```sh
   pgrep -fl "fred.py" || true
   ```

2. **Inspect the operation journal:**
   Read `operation.json` to identify what skills were being installed or updated:
   ```sh
   cat /path/to/project/.fred-skills/<host>-<scope>/operation.json
   ```
   Note the `skills`, `before`, `after_hashes`, and `backups` fields.

3. **Check the backups directory:**
   Verify the presence of the backup directory referenced in `operation.json`:
   ```sh
   ls -la /path/to/project/.fred-skills/<host>-<scope>/backups/
   ```

4. **Inspect the physical target directory:**
   Check the current state of skill packages in the target directory:
   ```sh
   # Example for claude-code:
   ls -la /path/to/project/.claude/skills/

   # Example for codex / antigravity:
   ls -la /path/to/project/.agents/skills/
   ```

### Step-by-Step Remediation

#### Scenario A: Automated Recovery for Interrupted Install or Update
When `operation.json` has `phase: "prepared"`:
1. Run `recover-install` with `--confirm-interrupted`:
   ```sh
   python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted
   ```
2. Expected response:
   ```json
   {
     "recovery": "restored-before-state",
     "skills": ["<skill-1>", "<skill-2>"],
     "preserved_interrupted_content": "/path/to/project/.fred-skills/<host>-<scope>/backups/recovered-<uuid>"
   }
   ```
3. Any partially written files are moved to `backups/recovered-<uuid>/` for forensic inspection rather than deleted.
4. Retry the original install or update command:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host <host> --pack starter
   ```

#### Scenario B: Journal/Receipt Disagreement or Modified Recovery Destination
If recovery fails with `Journal/receipt disagreement needs manual inspection` or `Modified recovery destination: <name>`:
This happens if files in the destination were modified by an editor or outside tool during the interruption, or if `receipt.json` was edited manually.
1. Inspect file differences between target and the backup:
   ```sh
   diff -r /path/to/project/.fred-skills/<host>-<scope>/backups/<operation-id>/<skill> \
           /path/to/project/<target_path>/<skill> || true
   ```
2. If the files in the target directory contain valuable custom edits, preserve them:
   ```sh
   cp -a /path/to/project/<target_path>/<skill> /tmp/saved-<skill>-custom
   ```
3. Restore the target skill directory from the pre-operation backup:
   ```sh
   rm -rf /path/to/project/<target_path>/<skill>
   cp -a /path/to/project/.fred-skills/<host>-<scope>/backups/<operation-id>/<skill> \
         /path/to/project/<target_path>/<skill>
   ```
4. Run the recovery command again to clear the journal and lock:
   ```sh
   python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted
   ```

#### Scenario C: Manual Recovery After Power Loss During Remove or Rollback
`remove` and `rollback` operations do not write an `operation.json` journal; they create a timestamped/UUID backup directory under `backups/`, move target skills into the backup, and atomically update `receipt.json`. If power is lost after files are moved but before `receipt.json` commits:
- Symptom: Target skill is missing on disk, but `receipt.json` still lists it as installed.
- Error when running any command: `fred-skills: Managed skill is missing: <name>; inspect the receipt before proceeding`.

**Remediation procedure:**
1. Locate the most recent backup folder in the state directory:
   ```sh
   ls -lat /path/to/project/.fred-skills/<host>-<scope>/backups/ | head -n 5
   ```
2. Identify the moved skill directory inside that backup (e.g., `backups/<uuid>/<skill-name>`).
3. Verify that the skill in the backup is intact:
   ```sh
   ls -la /path/to/project/.fred-skills/<host>-<scope>/backups/<uuid>/<skill-name>/SKILL.md
   ```
4. Move the skill package back from backup to the target directory:
   ```sh
   mv /path/to/project/.fred-skills/<host>-<scope>/backups/<uuid>/<skill-name> \
      /path/to/project/<target_path>/<skill-name>
   ```
5. Remove any stale lock file:
   ```sh
   rm -f /path/to/project/.fred-skills/<host>-<scope>/operation.lock
   ```
6. Re-run the desired remove or rollback command cleanly:
   ```sh
   python scripts/fred.py remove --destination /path/to/project --host <host> --skill <skill-name>
   ```

### Verification Steps

1. Confirm target skill directory exists and contains valid skill entry points:
   ```sh
   test -f /path/to/project/<target_path>/<skill-name>/SKILL.md && echo "Skill entry point verified"
   ```
2. Run a dry-run update to verify that all installed skills match receipt digests:
   ```sh
   python scripts/fred.py update --destination /path/to/project --host <host> --pack starter --dry-run
   ```

---

## Runbook 3: Receipt Validation Failure and Corrupted Receipt Reconstruction

### Context and Cause

The file `receipt.json` located at `<destination>/.fred-skills/<host>-<scope>/receipt.json` is the source of truth for managed skills, versions, and cryptographic hashes (`scripts/fredlib/installation.py:L123-L153`). Validation errors occur when:
- The receipt file is corrupted or contains invalid JSON syntax.
- Managed files are modified locally in the target directory (`Locally modified skill`).
- An unmanaged skill exists in the target directory with the same name (`Refusing to replace unmanaged skill`).
- A recorded skill is missing from the filesystem (`Managed skill is missing`).
- An unsafe path or symlink is detected (`Unsafe installed package path`).

### Symptoms

- Installation, update, or removal operations fail with exit code `1`.
- Specific error messages:
  - `fred-skills: Expecting value: line 1 column 1 (char 0)` (corrupted or empty JSON)
  - `fred-skills: Refusing to replace unmanaged skill: <name>`
  - `fred-skills: Locally modified skill: <name>; pass --force for managed recovery or preserve/move it before updating/removing`
  - `fred-skills: Managed skill is missing: <name>; pass --force for managed recovery or inspect the receipt before proceeding`
  - `fred-skills: Unsafe installed package path: <name>`

### Diagnostic Checks

1. **Validate JSON formatting of `receipt.json`:**
   ```sh
   python -m json.tool /path/to/project/.fred-skills/<host>-<scope>/receipt.json > /dev/null
   ```
   If this outputs a `JSONDecodeError`, the receipt file is malformed or truncated.

2. **Inspect the receipt structure and live status:**
   Check the current recorded skills, on-disk status (`tracked`, `modified`, `missing`, `untracked`), and hashes:
   ```sh
   # Inspect installation receipt, live status, and harness paths.
   # Target project root and host can be explicitly specified:
   python scripts/fred.py installed --destination /path/to/project --host <host> --scope <scope>

   # Output structured receipt and health fields in machine-readable JSON:
   python scripts/fred.py installed --destination /path/to/project --host <host> --scope <scope> --json

   # When running directly within the target project root directory,
   # --destination defaults to the current working directory:
   python scripts/fred.py installed --host <host> --scope <scope>
   python scripts/fred.py installed
   ```

3. **Check for workspace drift or active operation locks:**
   Run `installed --check` to verify live package hashes against `receipt.json` (exits `1` when drift or a lock exists):
   ```sh
   # Check a specific project and host (exits 0 when healthy, 1 on drift or lock):
   python scripts/fred.py installed --destination /path/to/project --host <host> --scope <scope> --check

   # Emit machine-readable JSON with per-skill status, untracked folders, locked, and healthy flags:
   python scripts/fred.py installed --destination /path/to/project --host <host> --check --json

   # Check the current working directory using auto-detected host markers or receipts:
   python scripts/fred.py installed --check
   ```
   In interactive terminals, `installed` renders a `Status` column classifying each entry:
   - `tracked`: Managed skill matches the SHA-256 `content_hash` recorded in `receipt.json`.
   - `modified`: Managed skill files on disk diverge from the recorded `content_hash`.
   - `missing`: Managed skill recorded in `receipt.json` is absent from the target directory.
   - `untracked`: Local `SKILL.md` folder in the target directory is not managed by `receipt.json`.

   Preview whether a planned repair will succeed before modifying files:
   ```sh
   python scripts/fred.py update --destination /path/to/project --host <host> --pack starter --force --dry-run
   ```
   Dry-run validation confirms host detection and confirms no unmanaged skill paths collide.

### Step-by-Step Remediation

#### Scenario A: Resolving Locally Modified Skills
If an update or removal is blocked by `Locally modified skill: <name>`:
1. Identify what files changed within the skill:
   ```sh
   diff -ru /path/to/fred-skills/skills/<name> /path/to/project/<target_path>/<name> || true
   ```
2. Choose one of two recovery routes:
   - **Route 1: Repair with `--force` (archives modified files to `.fred-skills/<host>-<scope>/backups/`):**
     Update or reinstall the managed skill cleanly while preserving your prior edits in backups:
     ```sh
     python scripts/fred.py update --destination /path/to/project --host <host> --skill <name> --force
     ```
     Or uninstall a locally modified managed skill after archiving its edited folder:
     ```sh
     python scripts/fred.py remove --destination /path/to/project --host <host> --skill <name> --force
     ```
   - **Route 2: Keep customizations as a standalone untracked skill:**
     Copy the customized skill to a custom folder name (which `installed` reports as `untracked`):
     ```sh
     cp -a /path/to/project/<target_path>/<name> /path/to/project/<target_path>/<name>-custom
     ```
3. Verify the workspace is healthy after recovery:
   ```sh
   python scripts/fred.py installed --destination /path/to/project --host <host> --check
   ```

#### Scenario B: Resolving Missing Managed Skills
If blocked by `Managed skill is missing: <name>`:
1. Restore missing managed skills in one step with `--force`:
   ```sh
   python scripts/fred.py install --destination /path/to/project --host <host> --pack starter --force
   ```
2. Or drop an intentionally deleted skill from `receipt.json` with `remove --force`:
   ```sh
   python scripts/fred.py remove --destination /path/to/project --host <host> --skill <name> --force
   ```
3. Confirm that `installed --check` now exits `0` with all remaining managed skills `tracked`.
4. Archived edits (if any) remain available under `.fred-skills/<host>-<scope>/backups/`.
5. Note: `--force` never modifies `untracked` user skill folders absent from `receipt.json`.
6. Note: `rollback` and `recover-install` do not accept `--force`; inspect backups before rolling back.
7. If `receipt.json` itself is corrupted rather than drifted, proceed to Scenario C below.

#### Scenario C: Non-Destructive Repair and Reconstruction of Corrupted `receipt.json`
**Never delete the `.fred-skills/` directory.** If `receipt.json` is missing or unparseable:
1. Back up the existing corrupted receipt:
   ```sh
   cp /path/to/project/.fred-skills/<host>-<scope>/receipt.json \
      /path/to/project/.fred-skills/<host>-<scope>/receipt.json.corrupt-bak
   ```
2. Reconstruct `receipt.json` by calculating current file hashes using `fredlib.common`:
   ```sh
   python -c "
   import sys, pathlib, json
   sys.path.insert(0, 'scripts')
   from fredlib.common import package_files, digest, atomic_json, version, ROOT

   destination = pathlib.Path('/path/to/project').resolve()
   host = '<host>'
   scope = 'project'
   state = destination / '.fred-skills' / f'{host}-{scope}'
   hosts = json.loads((ROOT / 'catalog/hosts.json').read_text())['hosts']
   target = destination / hosts[host][scope + '_path']
   source_ver = version(ROOT)

   # Scan target directory for valid skills
   skills_records = {}
   for child in sorted(target.iterdir()):
       if child.is_dir() and (child / 'SKILL.md').is_file() and not child.is_symlink():
           files = package_files(child)
           skills_records[child.name] = {
               'source_version': source_ver,
               'files': files,
               'content_hash': digest(files)
           }

   receipt_data = {
       'schema_version': '1.0',
       'host': host,
       'skills': skills_records,
       'history': []
   }

   # Re-attach history from previous corrupted receipt or backups if parseable
   try:
       old_receipt = json.loads((state / 'receipt.json.corrupt-bak').read_text())
       receipt_data['history'] = old_receipt.get('history', [])
   except Exception:
       pass

   atomic_json(state / 'receipt.json', receipt_data)
   print(f'Reconstructed receipt.json with {len(skills_records)} managed skills')
   "
   ```

### Verification Steps

1. Verify `receipt.json` is valid JSON and adheres to schema:
   ```sh
   python -m json.tool /path/to/project/.fred-skills/<host>-<scope>/receipt.json > /dev/null && echo "Receipt syntax valid"
   ```
2. Perform a dry-run update to confirm receipt and package agreement:
   ```sh
   python scripts/fred.py update --destination /path/to/project --host <host> --pack starter --dry-run
   ```

---

## Runbook 4: Git Revision Resolution and Audit Plan Failure Recovery

### Context and Cause

When running change-based audit planning (`scripts/fred.py plan` or `scripts/fred.py init`) with `--mode changes` or tasks requiring a base revision (`review-pr`), the toolkit resolves the Git revision and changed files in `scripts/fredlib/runs.py:L65-L71` and `scripts/fredlib/repository.py:L25-L42`, `L102-L135`.

Failure occurs when:
- `--base` revision argument is omitted.
- `git` command-line executable is missing from system `PATH`.
- The specified revision (branch, tag, or SHA) does not exist locally.
- A shallow clone lacks the necessary commit history to compute `git merge-base`.
- The specified revision does not resolve to a commit object (e.g., tree or blob reference).

### Symptoms

- Planning or initialization command fails immediately.
- Exit code: `1`
- Error messages on `stderr`:
  - `fred-skills: A change review requires --base <revision>; it will not silently audit the whole repository`
  - `fred-skills: Git is required for a revision/diff-based plan`
  - `fred-skills: Git could not resolve the requested repository or revision`
  - `fred-skills: Base did not resolve to a commit`

### Diagnostic Checks

1. **Verify Git executable availability:**
   ```sh
   which git && git --version
   ```
   If Git is not installed or not in PATH, the CLI cannot calculate repository differences.

2. **Verify repository directory is a Git repository:**
   ```sh
   git -C /path/to/repository rev-parse --is-inside-work-tree
   ```
   Must return `true`.

3. **Check if the repository is a shallow clone:**
   ```sh
   git -C /path/to/repository rev-parse --is-shallow-repository
   ```
   If `true`, Git may be unable to calculate `git merge-base` against upstream branches without unshallowing.

4. **Verify the candidate base revision resolves to a commit:**
   Test the exact Git command used by `scripts/fredlib/repository.py:L104-L111`:
   ```sh
   git -C /path/to/repository rev-parse --verify --end-of-options "<revision>^{commit}"
   ```
   If this outputs an error (e.g., `fatal: Needed a single revision`), `<revision>` is invalid or not fetched.

5. **Test merge-base computation against HEAD:**
   Test the exact Git command used by `scripts/fredlib/repository.py:L115`:
   ```sh
   git -C /path/to/repository merge-base "<revision>" HEAD
   ```
   If this command returns exit code 1, the branches share no common ancestor or shallow history excludes it.

### Step-by-Step Remediation

#### Scenario A: Missing `--base` Argument
If `fred.py plan` failed with `A change review requires --base <revision>`:
1. Supply an explicit base revision such as `main`, `origin/main`, `HEAD~1`, or a specific commit hash:
   ```sh
   python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base origin/main
   ```

#### Scenario B: Revision Not Found Locally (Unfetched Upstream Branch)
If `Git could not resolve the requested repository or revision` occurs because `main` or `origin/main` is not present locally:
1. Fetch the target branch from the remote:
   ```sh
   git -C /path/to/repository fetch origin main
   ```
2. Reference the remote-tracking ref explicitly:
   ```sh
   python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base origin/main
   ```

#### Scenario C: Shallow Clone Missing Merge Base (CI Environments)
In CI pipelines (such as GitHub Actions with default `fetch-depth: 1`):
1. Deepen or unshallow the repository history before running the audit plan:
   ```sh
   git -C /path/to/repository fetch --unshallow || git -C /path/to/repository fetch --deepen=100 origin main
   ```
2. Verify merge-base resolves:
   ```sh
   git -C /path/to/repository merge-base origin/main HEAD
   ```
3. Re-run the plan command.

#### Scenario D: Tag or Non-Commit Revision Passed
If `Base did not resolve to a commit` occurs:
1. Ensure the reference points to a commit object rather than a tree or blob:
   ```sh
   git -C /path/to/repository rev-parse --verify "<tag>^{commit}"
   ```
2. Pass the dereferenced commit SHA or standard branch name to `--base`.

#### Scenario E: Missing Git Executable
If `Git is required for a revision/diff-based plan` occurs:
1. Install Git via the system package manager (e.g., `apt-get install -y git`, `brew install git`, or `choco install git`).
2. Verify Git is in the executing process's `PATH`.

### Verification Steps

1. Confirm merge-base successfully outputs a commit SHA:
   ```sh
   git -C /path/to/repository merge-base "<base>" HEAD
   ```
2. Run `fred.py plan` in `changes` mode:
   ```sh
   python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base "<base>"
   ```
   Verify that the command succeeds with exit code `0` and outputs a valid JSON plan containing `changed_files` and `resolved_base`.

---

## Runbook 5: Diagnosing Stalled or Interrupted Audit Sessions via Session Triage

### Context and Cause

During long-running or autonomous repository audits, an agent session may stall, crash, or fail to produce an audit report. Common root causes include:
- Execution timeouts on external tools, build commands, or test suites.
- Schema validation mismatches where intermediate or output records fail schema invariants.
- Context window token exhaustion where prompt size or command output exceeds LLM context limits.
- Unexpected unhandled exceptions or system-level crashes during audit execution.

The session triage subsystem (`scripts/fred.py triage` and `skills/repo-audit-diagnostics/scripts/triage.py`) scans session transcripts and workspace artifacts to isolate the decisive failure signature and generate a sanitized diagnostic bundle and Markdown report.

### Core Operational Safeguards

1. **Strict preservation of canonical session ledgers:** Triage operates as a read-only analytical diagnostic tool. Modifying canonical session ledgers (`run.json`, `audit.json`) or mutating historical audit findings during triage remains strictly out of scope.
2. **Automated credential scrubbing:** Triage applies deterministic regex sanitizers (`skills/repo-audit-diagnostics/scripts/triage.py:L16-L60`) to redact sensitive authentication material prior to exporting bundles. This scrubs authorization headers (`Bearer`, `Basic`, `Token`), OpenAI/Anthropic API keys (`sk-...`), GitHub personal access tokens (`ghp_...`, `github_pat_...`), Slack tokens (`xox...`), AWS access keys (`AKIA...`), JWT tokens, private key blocks, and key-value secret definitions (`password`, `secret`, `api_key`).

### Symptoms

- An audit session ceases execution without producing the expected `audit.json` or `report.html`.
- CLI commands return exit code `1` or `2` during run execution.
- Session transcript ends abruptly or contains unhandled traceback or error lines.
- Triage CLI errors on `stderr`:
  - `fred-skills: Transcript file path is required`
  - `fred-skills: Transcript file not found: <path>`
  - `fred-skills: Triage script not found at <path>`
  - `fred-skills: Schema validation error: <details>`

### Diagnostic Checks and Procedures

1. **Identify the session transcript and workspace directory:**
   Locate the agent transcript file (`.json`, `.jsonl`, `.txt`, `.md`) and optional audit workspace containing `run.json` and `audit.json`:
   ```sh
   ls -la audit-output/bug/
   ```

2. **Execute session triage to extract scrubbed diagnostics:**
   Run `scripts/fred.py triage` pointing to the transcript and output directory:
   ```sh
   python scripts/fred.py triage audit-output/bug/session-transcript.jsonl --workspace audit-output/bug --output audit-output/bug/diagnostics
   ```

3. **Validate diagnostic bundle schema conformance:**
   Add `--check-schema` to ensure the generated `diagnostic_bundle.json` strictly satisfies `bundle.schema.json`:
   ```sh
   python scripts/fred.py triage audit-output/bug/session-transcript.jsonl --workspace audit-output/bug --output audit-output/bug/diagnostics --check-schema
   ```

4. **Interpret failure signatures and determine remediation:**
   Inspect `audit-output/bug/diagnostics/diagnostic_report.md` and `diagnostic_bundle.json` for the `failure_type`:

   #### Scenario A: Tool Execution Timeout (`tool_timeout`)
   If the report identifies `tool_timeout`:
   - Cause: A subprocess, test run, or repository search exceeded execution timeout limits.
   - Action: Increase tool execution timeout budget or subdivide large benchmark runs into smaller targeted scopes.

   #### Scenario B: Schema Validation Error (`schema_validation_error`)
   If the report identifies `schema_validation_error`:
   - Cause: Agent emitted malformed JSON or violated schema invariants (e.g., duplicate IDs, ungrounded findings, reversed line numbers).
   - Action: Inspect the matched error line in `diagnostic_bundle.json`, adjust prompt instructions or schema constraints, and resume the run.

   #### Scenario C: Context Window Token Limit Exceeded (`context_limit`)
   If the report identifies `context_limit`:
   - Cause: Cumulative prompt history or bulky tool outputs exceeded the model's context window.
   - Action: Configure exclusions (`--exclude`) for verbose paths, use `--budget brief` to restrict scope, or run audits incrementally across subsystems.

   #### Scenario D: Unclassified Failure (`unclassified_failure`)
   If the report identifies `unclassified_failure`:
   - Cause: Unhandled runtime exception or environmental defect outside recognized patterns.
   - Action: Review the sanitized stack trace in `diagnostic_report.md` to identify the failing module.

### Verification Steps

1. Confirm `diagnostic_bundle.json` and `diagnostic_report.md` are created in the output directory:
   ```sh
   ls -lh audit-output/bug/diagnostics/
   ```
2. Verify that no raw credentials appear in exported reports:
   ```sh
   grep -E "(sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{36,}|AKIA[0-9A-Z]{16})" audit-output/bug/diagnostics/* || echo "Credentials scrubbed successfully"
   ```
3. Confirm canonical session artifacts (`run.json`, `audit.json`) remain untouched:
   ```sh
   git status --short audit-output/bug/
   ```

---

## Operational Diagnostic Quick Reference

| Error Message | Exit Code | Source Location | Root Cause | Immediate Action |
| --- | --- | --- | --- | --- |
| `Another install operation or interrupted operation holds the lock` | 1 | `installation.py:L113` | Lock file exists from active or crashed command | Check `pgrep -fl fred.py`; run `recover-install --confirm-interrupted` |
| `Journal/receipt disagreement needs manual inspection` | 1 | `installation.py:L408` | Filesystem or receipt modified during interrupted run | Diff target vs `backups/<id>`; restore before running recovery |
| `Modified recovery destination: <name>` | 1 | `installation.py:L421` | Files modified during or after interrupted install | Backup modifications to `/tmp`; restore matching before/after hash |
| `Refusing to replace unmanaged skill: <name>` | 1 | `installation.py:L144` | Directory exists at target but missing from receipt | Review skill directory; reconstruct `receipt.json` or move directory |
| `Locally modified skill: <name>` | 1 | `installation.py:L146` | Files modified in target after installation | Diff against source; revert local changes or rename custom skill |
| `Managed skill is missing: <name>` | 1 | `installation.py:L150` | Skill in receipt but missing from disk | Restore from `backups/` or re-copy from source repository |
| `A change review requires --base <revision>` | 1 | `runs.py:L68` | `--mode changes` specified without `--base` | Pass `--base <ref>` (e.g. `--base origin/main`) |
| `Git could not resolve the requested repository or revision` | 1 | `repository.py:L38` | Base branch not fetched, invalid ref, or shallow clone | Run `git fetch origin <branch>`; test `git merge-base` |
| `Base did not resolve to a commit` | 1 | `repository.py:L113` | Ref points to non-commit object | Use commit SHA or `<tag>^{commit}` |
| `Git is required for a revision/diff-based plan` | 1 | `repository.py:L28` | `git` executable not found in `PATH` | Install Git and add to `PATH` |
| `Transcript file path is required` | 1 | `fred.py:L785` | Transcript positional or `--transcript` argument omitted | Pass transcript path: `python scripts/fred.py triage <transcript>` |
| `Transcript file not found: <path>` | 1 | `fred.py:L787` | Transcript path does not exist or unreadable | Check file path and read permissions |
| `Triage script not found at <path>` | 1 | `fred.py:L795` | `repo-audit-diagnostics` skill package missing or moved | Reinstall `repo-audit-diagnostics` package via `fred.py install` |
| `Schema validation error: <details>` | 1 | `triage.py:L459` | Generated bundle violates `bundle.schema.json` | Review transcript format or run without `--check-schema` for raw dump |
| `Tool execution timed out: <cmd>` | 0 | `triage.py:L167` | Tool/subprocess execution deadline exceeded during audit | Increase tool timeout budget or isolate hanging command |
| `Audit schema validation error: <msg>` | 0 | `triage.py:L178` | Agent output violated audit schema invariants | Review transcript schema mismatch line and repair prompt constraints |
| `Context window token limit exceeded: <msg>` | 0 | `triage.py:L189` | Model context window limit reached | Use `--budget brief`, exclude verbose paths, or split audit run |
| `Unclassified execution failure: <line>` | 0 | `triage.py:L200` | Unhandled traceback or runtime error in transcript | Review sanitized traceback in `diagnostic_report.md` |
