# Operational Runbooks and Troubleshooting Guide

This guide provides operational troubleshooting runbooks and disaster recovery procedures for `fred-skills` installation, state management, planning, session triage, run validation, and finding reconciliation workflows. It covers lock contention, interrupted installations, corrupted receipts, Git revision resolution, stalled session triage, run ledger validation, and remediation proof checks.

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

The installation subsystem acquires an exclusive lock (`operation.lock`) via `os.open` with `O_CREAT | O_EXCL` flags in `fredlib.installation.lock` during `install`, `update`, `remove`, and `rollback` operations. If a previous invocation was killed, crashed, or disconnected, the lock file remains on disk, blocking subsequent operations.

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
   - `"phase": "prepared"`: Interrupted during staging or swap; run `recover-install --confirm-interrupted` (`fredlib.installation.recover`).
   - `"phase": "committed"`: Swap and receipt committed before lock unlink; run `recover-install --confirm-interrupted` (`fredlib.installation._check_already_committed_recovery`).
   - `"phase": "recovered"`: Prior recovery already finished (`fredlib.installation._validate_recovery_journal` rejects non-`"prepared"` journals); confirm no writer runs and delete `operation.lock` manually (Scenario C).
   - File does not exist or is empty: Aborted before journal initialization (`fredlib.installation._load_recovery_journal` requires `operation.json`); confirm no writer runs and delete `operation.lock` manually (Scenario C).

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
     "skills": ["<skill-1>", "<skill-2>"]
   }
   ```

#### Scenario C: Stale Lock with Missing or `"recovered"` Journal
`recover-install` requires a `"prepared"` or `"committed"` `operation.json` and rejects missing or `"recovered"` journals (`fredlib.installation._load_recovery_journal`, `fredlib.installation._validate_recovery_journal`). After confirming via `pgrep` that no `fred.py` writer is running:
1. Verify no process holds an open file descriptor on `operation.lock`:
   ```sh
   lsof /path/to/project/.fred-skills/<host>-<scope>/operation.lock 2>/dev/null || true
   ```
2. Safely remove only the stale lock file (preserving `receipt.json`, `operation.json`, and `backups/`):
   ```sh
   rm /path/to/project/.fred-skills/<host>-<scope>/operation.lock
   ```

### Verification Steps

1. Confirm the lock file is removed:
   ```sh
   test ! -f /path/to/project/.fred-skills/<host>-<scope>/operation.lock && echo "Lock successfully cleared"
   ```
2. Verify lock clearance and installation health (`fredlib.installation._is_state_locked`, `fred.main`):
   ```sh
   python scripts/fred.py installed --destination /path/to/project --host <host> --check
   ```
   `installed --check` exits `0` when unlocked and healthy, or `1` if `operation.lock` or drift remains (unlike `fredlib.installation.install`, which returns early on `--dry-run` before acquiring `operation.lock`).

---

## Runbook 2: Interrupted Installation and Power-Loss Recovery

### Context and Cause

An installation or update operation consists of staging files, creating a backup of pre-existing skills, atomically recording a journal (`operation.json`), replacing target directories, committing `receipt.json`, and finally updating the journal phase to `committed`. If power loss, SIGKILL, or hardware interruption occurs during this window (`fredlib.installation._execute_install`), the destination skills directory may be in a partially swapped state.

Similarly, `remove` and `rollback` operations (`fredlib.installation._execute_remove_operation`, `fredlib.installation._execute_rollback_operation`) move skills between the target directory and `backups/`. A power loss during these operations can result in skills residing in `backups/` while `receipt.json` remains unupdated.

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
2. Run a dry-run update without `--pack` so `fredlib.installation._select_install_skills` verifies every installed skill in `receipt.json`:
   ```sh
   python scripts/fred.py update --destination /path/to/project --host <host> --dry-run
   ```

---

## Runbook 3: Receipt Validation Failure and Corrupted Receipt Reconstruction

### Context and Cause

The file `receipt.json` located at `<destination>/.fred-skills/<host>-<scope>/receipt.json` is the source of truth for managed skills, versions, and cryptographic hashes (`fredlib.installation.receipt`, `fredlib.installation.require_unmodified`, `fredlib.installation.checked_target`). Validation errors occur when:
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
1. Back up the existing corrupted receipt only when `receipt.json` exists on disk:
   ```sh
   test -f /path/to/project/.fred-skills/<host>-<scope>/receipt.json && \
     cp /path/to/project/.fred-skills/<host>-<scope>/receipt.json /path/to/project/.fred-skills/<host>-<scope>/receipt.json.corrupt-bak || true
   ```
2. Reconstruct `receipt.json` for catalog-recognized skills (`fredlib.common.choose_skills`) using `fredlib.common`:
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
   catalog_skills = {s['name'] for s in json.loads((ROOT / 'catalog/skills.json').read_text())['skills']}
   target = destination / hosts[host][scope + '_path']
   source_ver = version(ROOT)

   # Scan target directory for catalog-recognized skills (keep custom skills untracked)
   skills_records = {}
   for child in (sorted(target.iterdir()) if target.is_dir() else []):
       if child.name in catalog_skills and child.is_dir() and (child / 'SKILL.md').is_file() and not child.is_symlink():
           files = package_files(child)
           skills_records[child.name] = {
               'source_version': source_ver,
               'files': files,
               'content_hash': digest(files)
           }

   receipt_data = {'schema_version': '1.0', 'host': host, 'skills': skills_records, 'history': []}

   # Re-attach history from previous corrupted receipt if parseable
   if (state / 'receipt.json.corrupt-bak').is_file():
       try:
           old_receipt = json.loads((state / 'receipt.json.corrupt-bak').read_text())
           receipt_data['history'] = old_receipt.get('history', [])
       except Exception:
           pass

   state.mkdir(parents=True, exist_ok=True)
   atomic_json(state / 'receipt.json', receipt_data)
   print(f'Reconstructed receipt.json with {len(skills_records)} managed skills')
   "
   ```

### Verification Steps

1. Verify `receipt.json` is valid JSON and adheres to schema:
   ```sh
   python -m json.tool /path/to/project/.fred-skills/<host>-<scope>/receipt.json > /dev/null && echo "Receipt syntax valid"
   ```
2. Perform a dry-run update without `--pack` so `fredlib.installation._select_install_skills` validates every skill recorded in `receipt.json` while custom folders remain `untracked`:
   ```sh
   python scripts/fred.py update --destination /path/to/project --host <host> --dry-run
   ```

---

## Runbook 4: Git Revision Resolution and Audit Plan Failure Recovery

### Context and Cause

When running change-based audit planning (`scripts/fred.py plan` or `scripts/fred.py init`) with `--mode changes` or tasks requiring a base revision (`review-pr`), the toolkit resolves the Git revision and changed files in `fredlib.runs.plan`, `fredlib.repository.git`, and `fredlib.repository.changed_files`.

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
   Test the exact Git command used by `fredlib.repository.changed_files`:
   ```sh
   git -C /path/to/repository rev-parse --verify --end-of-options "<revision>^{commit}"
   ```
   If this outputs an error (e.g., `fatal: Needed a single revision`), `<revision>` is invalid or not fetched.

5. **Test merge-base computation against HEAD:**
   Test the exact merge-base command used by `fredlib.repository.changed_files`:
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
2. Run `fred.py plan` in `changes` mode with `--json` (`fred.main` renders human tables on interactive TTYs unless `--json` is passed):
   ```sh
   python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base "<base>" --json
   ```
   Verify that the command succeeds with exit code `0` and outputs a valid JSON object containing `plan.base_revision` and `plan.changed_files` (`fredlib.runs.plan`).

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
2. **Automated key-aware and pattern-based credential scrubbing:** Triage applies recursive JSON/escaped-string key inspection (`triage._SENSITIVE_KEY_EXACT`, `triage._is_sensitive_json_key`, `triage.scrub_credentials_with_stats`) alongside categorized regex sanitizers (`triage.CREDENTIAL_PATTERNS_CATEGORIZED`) before exporting bundles. This redacts values assigned to sensitive keys (`password`, `secret`, `api_key`, `access_token`, `authorization`, `cookie`, `private_key`) even inside nested or stringified JSON payloads, as well as authorization headers (`Bearer`, `Basic`, `Token`), OpenAI/Anthropic API keys (`sk-...`), GitHub tokens (`ghp_...`, `github_pat_...`), Slack tokens (`xox...`), AWS access keys (`AKIA...`), JWTs, and PEM private keys.
3. **Configurable redaction tiers (`--redaction-tier`):** Operators control how much tool output is retained via `fred.parser`, `triage.REDACTION_TIERS`, and `triage.OMITTED_TOOL_OUTPUT_PLACEHOLDER`:
   - `skeleton`: Replaces all tool outputs with `[OMITTED_TOOL_OUTPUT]`, preserving only turn structure, failure classification, and scrubbed error summaries for safe external sharing.
   - `evidence`: Retains scrubbed tool outputs only on turns cited as decisive failure evidence and replaces unrelated tool outputs with `[OMITTED_TOOL_OUTPUT]`.
   - `full` (default): Retains all tool outputs after recursive key-aware and regex credential scrubbing.

### Symptoms

- An audit session ceases execution without producing the expected `audit.json` or `report.html`.
- CLI commands return exit code `1` or `2` during run execution.
- Session transcript ends abruptly or contains unhandled traceback or error lines.
- Triage CLI errors on `stderr`:
  - `fred-skills: Transcript file path is required`
  - `fred-skills: Workspace directory not found: <path>`
  - `fred-skills: Transcript file not found: <path>`
  - `fred-skills: Triage script not found at <path>`
  - `fred-skills: Schema validation error: <details>`

### Diagnostic Checks and Procedures

1. **Identify the session transcript or rely on automatic discovery:**
   Check the audit workspace directory (`run.json`, `audit.json`, and any local transcript logs):
   ```sh
   ls -la audit-output/bug/
   ```
   When the positional `<transcript>` argument is omitted, `fred.handle_diagnostics` invokes `triage.discover_transcript_details` to locate the latest matching session transcript automatically from `--workspace` (searching workspace logs and host session stores for Claude Code, Codex, Gemini CLI, and Cursor) or directly by `--session-id`.

2. **Execute session triage (explicit transcript or automatic discovery):**
   Run `scripts/fred.py triage` with an explicit transcript file, or omit `<transcript>` to auto-discover via `--workspace` or `--session-id`:
   ```sh
   # Explicit transcript path:
   python scripts/fred.py triage audit-output/bug/session-transcript.jsonl --workspace audit-output/bug --output audit-output/bug/diagnostics

   # Automatic transcript discovery from workspace directory:
   python scripts/fred.py triage --workspace audit-output/bug --output audit-output/bug/diagnostics

   # Automatic transcript discovery by host session ID:
   python scripts/fred.py triage --session-id <session-id> --output audit-output/bug/diagnostics
   ```

3. **Select a `--redaction-tier` and validate bundle schema conformance:**
   Choose `skeleton`, `evidence`, or `full` (`triage.REDACTION_TIERS`) and pass `--check-schema` (`triage.validate_bundle`) to verify `diagnostic_bundle.json` against `bundle.schema.json`:
   ```sh
   # Minimal shareable bundle (all tool outputs omitted):
   python scripts/fred.py triage --workspace audit-output/bug --output audit-output/bug/diagnostics --redaction-tier skeleton --check-schema

   # Retain only failure-cited tool outputs:
   python scripts/fred.py triage --workspace audit-output/bug --output audit-output/bug/diagnostics --redaction-tier evidence --check-schema

   # Full scrubbed diagnostic bundle (default tier):
   python scripts/fred.py triage --workspace audit-output/bug --output audit-output/bug/diagnostics --redaction-tier full --check-schema
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

## Runbook 6: Run Workspace and Ledger Integrity Recovery (`validate-run`)

### Context and Cause

The `validate-run` command (`fred.parser`, `fred.handle_runs`, `fredlib.runs.validate_run`) enforces structural and cryptographic invariants across an initialized run workspace (`run.json`, primary deliverable such as `audit.json`, and optional workflow companion artifacts such as `remediation.json` or `verification.json`). Specifically, `fredlib.runs.validate_run` and `fredlib.runs.audit_errors` verify:
- `run.json` conforms to `schemas/run.schema.json`.
- The suite deliverable (`audit.json` or domain deliverable) satisfies its JSON schema and semantic invariants (unique IDs, grounded evidence references, repository-relative paths).
- `run.snapshot.digest` equals the canonical SHA-256 digest of `{files, skipped, exclude}` (`fredlib.common.digest`).
- `audit.run_id` matches `run.run_id`, and `audit.repository.snapshot` matches `run.snapshot.digest` (or a recorded `loop.snapshot_history` entry).
- Any present workflow companion files (`remediation.json`, `verification.json`) conform to their schemas (`fredlib.runs._validate_present_workflow_companions`).

### Symptoms

- `python scripts/fred.py validate-run <dir>` or `advance` fails with exit code `1` (`schema_validation_failed` in `fred.handle_runs`).
- Error messages on `stderr`:
  - `fred-skills: Snapshot content identity does not match its recorded file inventory`
  - `fred-skills: Run and audit IDs differ`
  - `fred-skills: Audit evidence is not tied to the current run snapshot`
  - `fred-skills: <field/schema errors in run.json, audit.json, or companion artifacts>`

### Diagnostic Checks

1. **Run `validate-run` with `--json` to inspect structured error details:**
   ```sh
   python scripts/fred.py validate-run audit-output/bug --json
   ```
2. **Compare `run_id` and `snapshot` fields across `run.json` and `audit.json`:**
   ```sh
   python -c "
   import json, pathlib
   run_dir = pathlib.Path('audit-output/bug')
   run = json.loads((run_dir / 'run.json').read_text()) if (run_dir / 'run.json').is_file() else {}
   audit = json.loads((run_dir / 'audit.json').read_text()) if (run_dir / 'audit.json').is_file() else {}
   print('run.run_id:', run.get('run_id'), '| audit.run_id:', audit.get('run_id'))
   print('run.snapshot:', run.get('snapshot', {}).get('digest'), '| audit.snapshot:', audit.get('repository', {}).get('snapshot'))
   "
   ```

### Step-by-Step Remediation

#### Scenario A: Snapshot Digest Drift (`Snapshot content identity does not match its recorded file inventory`)
This occurs if `run.json` had `snapshot.files`, `snapshot.skipped`, or `snapshot.exclude` edited manually without recomputing `snapshot.digest`.
1. Recompute `run.snapshot.digest` from the recorded inventory using `fredlib.common.digest` and update `audit.repository.snapshot` if tied to the same run:
   ```sh
   python -c "
   import sys, json, pathlib
   sys.path.insert(0, 'scripts')
   from fredlib.common import digest, atomic_json

   run_dir = pathlib.Path('audit-output/bug')
   if (run_dir / 'run.json').is_file():
       run = json.loads((run_dir / 'run.json').read_text())
       snap = run['snapshot']
       snap['digest'] = digest({k: snap[k] for k in ('files', 'skipped', 'exclude')})
       atomic_json(run_dir / 'run.json', run)
       if (run_dir / 'audit.json').is_file():
           audit = json.loads((run_dir / 'audit.json').read_text())
           audit.setdefault('repository', {})['snapshot'] = snap['digest']
           atomic_json(run_dir / 'audit.json', audit)
   "
   ```
2. If instead repository source files changed on disk after `init`, do not hand-edit `snapshot.files`; run `resume` (`fredlib.runs.revalidate`) to record `revalidation.json` and identify stale findings:
   ```sh
   python scripts/fred.py resume audit-output/bug --json
   ```

#### Scenario B: Run ID or Snapshot Mismatch (`Run and audit IDs differ` / `Audit evidence is not tied to the current run snapshot`)
This occurs when an `audit.json` file from another run workspace was copied into `audit-output/bug/` or emitted with placeholder metadata.
1. Verify the findings in `audit.json` actually belong to the repository revision recorded in `run.json`. If `audit.json` came from a different run, move it back to its originating run directory rather than overwriting IDs blindly.
2. When an agent wrote findings for the current workspace but stamped a placeholder `run_id` or `repository.snapshot`, synchronize `audit.json` identity headers from `run.json`:
   ```sh
   python -c "
   import sys, json, pathlib
   sys.path.insert(0, 'scripts')
   from fredlib.common import atomic_json

   run_dir = pathlib.Path('audit-output/bug')
   if (run_dir / 'run.json').is_file() and (run_dir / 'audit.json').is_file():
       run = json.loads((run_dir / 'run.json').read_text())
       audit = json.loads((run_dir / 'audit.json').read_text())
       audit['run_id'] = run['run_id']
       audit.setdefault('repository', {})['snapshot'] = run['snapshot']['digest']
       atomic_json(run_dir / 'audit.json', audit)
   "
   ```

#### Scenario C: Ledger or Companion Schema Validation Errors
If `run.json`, `audit.json`, or a companion file (`remediation.json`) fails schema validation (`fredlib.runs.audit_errors`, `fredlib.runs._schema_errors`):
1. Check JSON syntax across workspace ledgers:
   ```sh
   python -m json.tool audit-output/bug/run.json > /dev/null
   python -m json.tool audit-output/bug/audit.json > /dev/null
   ```
2. Repair the reported field constraint in `audit.json` (for example, ensuring every `finding.evidence_ids` entry exists in `evidence`, `line_start <= line_end`, and finding IDs are unique) or in `run.json` against `schemas/run.schema.json`.

### Verification Steps

1. Run `validate-run` and confirm exit code `0` with `{"valid": true, "semantic_claims_verified": false}`:
   ```sh
   python scripts/fred.py validate-run audit-output/bug --json
   ```

---

## Runbook 7: Finding Remediation State and Verification Proof Recovery (`reconcile`)

### Context and Cause

The `reconcile` command (`fred.parser`, `fred.handle_runs`, `fredlib.runs.reconcile`) records lifecycle transitions (`open`, `in-progress`, `fixed-pending-verification`, `verified-fixed`, `regressed`, `accepted-risk`) in `<run-dir>/remediation.json` keyed by finding ID and stable root-cause fingerprint (`fredlib.runs.fingerprint`). Transitioning a finding to `verified-fixed` enforces strict proof gates against `skills/repo-verify-fix/assets/verification.schema.json`, while `remediation.json` is validated against `skills/repo-reconcile-findings/assets/reconciliation.schema.json`.

### Symptoms

- `python scripts/fred.py reconcile ...` exits with code `1`.
- Error messages on `stderr`:
  - `fred-skills: Unknown finding or remediation state`
  - `fred-skills: verified-fixed requires a verification JSON artifact`
  - `fred-skills: Verification artifact does not satisfy the proof schema: <details>`
  - `fred-skills: Verification must record before/after source, executed reproduction/regression, safe controls, commands, and result`
  - `fred-skills: Malformed remediation ledger '<path>': 'records' must be a JSON array`
  - `fred-skills: Remediation ledger does not satisfy reconciliation.schema.json: <details>`

### Diagnostic Checks

1. **List available finding IDs in `audit.json`:**
   ```sh
   python -c "
   import json, pathlib
   audit_file = pathlib.Path('audit-output/bug/audit.json')
   findings = json.loads(audit_file.read_text()).get('findings', []) if audit_file.is_file() else []
   print([f.get('id') for f in findings])
   "
   ```
2. **Inspect any candidate `verification.json` artifact:**
   ```sh
   python -m json.tool audit-output/bug/verification.json
   ```

### Step-by-Step Remediation

#### Scenario A: Unknown Finding ID or Unsupported Remediation State
If `reconcile` fails with `Unknown finding or remediation state`:
1. Confirm the `--finding` ID matches an exact `findings[].id` entry in `audit-output/bug/audit.json` (`fredlib.runs._require_audit_directory`).
2. Use one of the six supported `--state` values (`open`, `in-progress`, `fixed-pending-verification`, `verified-fixed`, `regressed`, `accepted-risk`):
   ```sh
   python scripts/fred.py reconcile audit-output/bug --finding F-1 --state in-progress --note "Investigating root cause and reproduction test"
   ```

#### Scenario B: Missing or Incomplete Proof Artifact for `verified-fixed`
`fredlib.runs.reconcile` refuses to mark a finding `verified-fixed` unless `--verification <path>` points to a JSON object that satisfies `skills/repo-verify-fix/assets/verification.schema.json` with `original_reproduced: true`, `regression_passed: true`, `safe_controls_passed: true`, and a non-empty `commands` array.
1. If fixes are applied but reproduction/regression checks have not yet been executed, record `fixed-pending-verification` instead:
   ```sh
   python scripts/fred.py reconcile audit-output/bug --finding F-1 --state fixed-pending-verification --note "Patch applied; awaiting reproduction and regression verification"
   ```
2. Once before/after reproduction, regression tests, and safe-control tests pass, write a complete `verification.json` artifact:
   ```json
   {
     "before_revision": "a1b2c3d",
     "after_revision": "e4f5g6h",
     "original_reproduced": true,
     "regression_passed": true,
     "safe_controls_passed": true,
     "commands": [
       "python -m unittest tests.test_regression"
     ],
     "result": "Original failure reproduced prior to fix; regression and safe-control tests pass on patched revision."
   }
   ```
3. Run `reconcile` with `--state verified-fixed` and `--verification`:
   ```sh
   python scripts/fred.py reconcile audit-output/bug --finding F-1 --state verified-fixed --verification audit-output/bug/verification.json --note "Verified fix with reproduction and safe controls"
   ```

#### Scenario C: Malformed `remediation.json` Ledger
If `remediation.json` was truncated or manually edited so `records` is missing or violates `reconciliation.schema.json`:
1. Back up the malformed file before repairing:
   ```sh
   test -f audit-output/bug/remediation.json && cp audit-output/bug/remediation.json audit-output/bug/remediation.json.corrupt-bak || true
   ```
2. Restore valid `schema_version` (`"1.0"`) and `records` array entries in `audit-output/bug/remediation.json`, then re-run `reconcile`.

### Verification Steps

1. Validate both `remediation.json` and the full run workspace via `validate-run` (`fredlib.runs._validate_present_workflow_companions`):
   ```sh
   python scripts/fred.py validate-run audit-output/bug --json
   ```

---

## Operational Diagnostic Quick Reference

| Error Message | Exit Code | Source Symbol | Root Cause | Immediate Action |
| --- | --- | --- | --- | --- |
| `Another install operation or interrupted operation holds the lock` (`"prepared"` or `"committed"` journal) | 1 | `fredlib.installation.lock` | Lock file remains from an interrupted `"prepared"` or `"committed"` operation | Check `pgrep -fl fred.py`; run `recover-install --confirm-interrupted`; verify with `installed --check` |
| `Another install operation or interrupted operation holds the lock` (missing or `"recovered"` journal) | 1 | `fredlib.installation._validate_recovery_journal` | Stale `operation.lock` with missing `operation.json` or `"phase": "recovered"` | Confirm no `fred.py` process runs (`pgrep`/`lsof`), `rm operation.lock`, then run `installed --check` |
| `No interrupted installation journal 'operation.json' found` | 1 | `fredlib.installation._load_recovery_journal` | `recover-install` invoked when `operation.json` is absent | Check active processes, remove stale `operation.lock` manually, and verify with `installed --check` |
| `Journal/receipt disagreement needs manual inspection` | 1 | `fredlib.installation._validate_recovery_journal` | Non-`"prepared"` journal or modified receipt during interrupted run | If `"recovered"`, remove stale `operation.lock`; otherwise diff target vs `backups/<id>` before recovery |
| `Modified recovery destination: <name>` | 1 | `fredlib.installation._validate_recovery_skill` | Files modified during or after interrupted install | Backup modifications to `/tmp`; restore matching before/after hash from `backups/<id>` |
| `Refusing to replace unmanaged skill: <name>` | 1 | `fredlib.installation.require_unmodified` | Directory exists at target but missing from receipt | Review skill directory; reconstruct catalog entries in `receipt.json` or rename custom folder |
| `Locally modified skill: <name>` | 1 | `fredlib.installation.require_unmodified` | Files modified in target after installation | Run `update --force` (archives backup) or copy edits to `<name>-custom` |
| `Managed skill is missing: <name>` | 1 | `fredlib.installation.require_unmodified` | Skill in receipt but missing from disk | Restore with `install --force` or drop from receipt with `remove --force` |
| `A change review requires --base <revision>` | 1 | `fredlib.runs.plan` | `--mode changes` specified without `--base` | Pass `--base <ref>` and `--json` to inspect `plan.base_revision` |
| `Git could not resolve the requested repository or revision` | 1 | `fredlib.repository.git` | Base branch not fetched, invalid ref, or shallow clone | Run `git fetch origin <branch>`; test `git merge-base` |
| `Base did not resolve to a commit` | 1 | `fredlib.repository.changed_files` | Ref points to non-commit object | Use commit SHA or `<tag>^{commit}` |
| `Git is required for a revision/diff-based plan` | 1 | `fredlib.repository.git` | `git` executable not found in `PATH` | Install Git and add to `PATH` |
| `Transcript file path is required` | 1 | `fred.handle_diagnostics` | Transcript, `--workspace`, and `--session-id` all omitted | Pass `<transcript>`, `--workspace <dir>`, or `--session-id <id>` |
| `Workspace directory not found: <path>` | 1 | `fred.handle_diagnostics` | `--workspace` directory does not exist | Verify the run workspace directory path before running `triage` |
| `Transcript file not found: <path>` | 1 | `fred.handle_diagnostics` | Explicit transcript path does not exist or unreadable | Check file path or omit `<transcript>` with `--workspace` / `--session-id` |
| `Triage script not found at <path>` | 1 | `fred._load_triage_module` | `repo-audit-diagnostics` skill package missing or moved | Restore `skills/repo-audit-diagnostics/scripts/triage.py` |
| `Schema validation error: <details>` | 1 | `triage.validate_bundle` | Generated bundle violates `bundle.schema.json` | Inspect mismatch or re-run with `--redaction-tier skeleton` |
| `Tool execution timed out: <cmd>` | 0 | `triage.TIMEOUT_PATTERNS` | Tool/subprocess execution deadline exceeded during audit | Increase tool timeout budget or isolate hanging command |
| `Audit schema validation error: <msg>` | 0 | `triage.SCHEMA_PATTERNS` | Agent output violated audit schema invariants | Review transcript schema mismatch line and repair prompt constraints |
| `Context window token limit exceeded: <msg>` | 0 | `triage.CONTEXT_PATTERNS` | Model context window limit reached | Use `--budget brief`, exclude verbose paths, or split audit run |
| `Unclassified execution failure: <line>` | 0 | `triage._build_unclassified_from_turn` | Unhandled traceback or runtime error in transcript | Review sanitized traceback in `diagnostic_report.md` |
| `Snapshot content identity does not match its recorded file inventory` | 1 | `fredlib.runs.validate_run` | `run.snapshot.digest` drifted from `{files, skipped, exclude}` | Recompute `snapshot.digest` via `fredlib.common.digest` or run `fred.py resume` |
| `Run and audit IDs differ` | 1 | `fredlib.runs.validate_run` | `audit.run_id` does not match `run.run_id` | Verify `audit.json` belongs to the run and align `run_id` |
| `Audit evidence is not tied to the current run snapshot` | 1 | `fredlib.runs.validate_run` | `audit.repository.snapshot` absent from run snapshot(s) | Run `fred.py resume` or synchronize `audit.repository.snapshot` with `run.snapshot.digest` |
| `Unknown finding or remediation state` | 1 | `fredlib.runs.reconcile` | Finding ID missing in `audit.json` or invalid `--state` | Check `findings[].id` in `audit.json` and pass a valid remediation `--state` |
| `verified-fixed requires a verification JSON artifact` | 1 | `fredlib.runs.reconcile` | `--state verified-fixed` passed without `--verification` | Use `fixed-pending-verification` or pass `--verification <verification.json>` |
| `Verification artifact does not satisfy the proof schema` | 1 | `fredlib.runs.reconcile` | `--verification` JSON violates `verification.schema.json` | Include required fields (`before_revision`, `after_revision`, booleans, `commands`, `result`) |
| `Verification must record before/after source, executed reproduction/regression, safe controls, commands, and result` | 1 | `fredlib.runs.reconcile` | Proof booleans not `true` or `commands` empty | Execute reproduction, regression, and safe-control checks and set all three booleans `true` |
| `Remediation ledger does not satisfy reconciliation.schema.json` | 1 | `fredlib.runs.reconcile` | `remediation.json` violates `reconciliation.schema.json` | Back up `remediation.json` and repair malformed `records` entries |
