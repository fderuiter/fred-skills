# Developer API Guide

This document provides the authoritative programmatic API reference for the six library modules in `scripts/fredlib/`. These modules provide reusable, scriptable components for run lifecycle management, skill installation, evaluation benchmarking, HTML/draft reporting, release packaging, and repository inspection.

For command-line usage and CLI workflows, see the [Local Toolkit Guide](toolkit.md). For conceptual design and security boundaries, see the [Architecture Guide](architecture.md).

---

## Table of Contents

1. [Overview and Import Conventions](#overview-and-import-conventions)
2. [Module: `fredlib.runs`](#module-fredlibruns)
   - [`now`](#now)
   - [`plan`](#plan)
   - [`empty_audit`](#empty_audit)
   - [`initialize`](#initialize)
   - [`prompt`](#prompt)
   - [`audit_errors`](#audit_errors)
   - [`validate_run`](#validate_run)
   - [`checkpoint`](#checkpoint)
   - [`revalidate`](#revalidate)
   - [`fingerprint`](#fingerprint)
   - [`compare`](#compare)
   - [`reconcile`](#reconcile)
   - [`memory`](#memory)
   - [`import_sarif`](#import_sarif)
3. [Module: `fredlib.installation`](#module-fredlibinstallation)
   - [`detect_host`](#detect_host)
   - [`locations`](#locations)
   - [`lock`](#lock)
   - [`receipt`](#receipt)
   - [`checked_target`](#checked_target)
   - [`require_unmodified`](#require_unmodified)
   - [`install`](#install)
   - [`remove`](#remove)
   - [`rollback`](#rollback)
   - [`recover`](#recover)
4. [Module: `fredlib.evaluations`](#module-fredlibevaluations)
   - [`evaluate`](#evaluate)
5. [Module: `fredlib.reports`](#module-fredlibreports)
   - [`text`](#text)
   - [`render`](#render)
   - [`issue_drafts`](#issue_drafts)
6. [Module: `fredlib.releases`](#module-fredlibreleases)
   - [`build`](#build)
   - [`verify`](#verify)
7. [Module: `fredlib.repository`](#module-fredlibrepository)
   - [`git`](#git)
   - [`snapshot`](#snapshot)
   - [`changed_files`](#changed_files)
   - [`preflight`](#preflight)

---

## Overview and Import Conventions

The toolkit's programmatic package is located at `scripts/fredlib`. Scripts can import either individual modules or the package root:

```python
import sys
from pathlib import Path

# Ensure the scripts directory is on sys.path
sys.path.insert(0, str(Path("/path/to/fred-skills/scripts")))

from fredlib import evaluations, installation, releases, reports, repository, runs
```

All functions adhere to predictable error handling:
- Invalid arguments, unresolvable paths, schema mismatches, or locked states raise `ValueError`.
- Missing or inaccessible files raise standard `OSError` or `FileNotFoundError`.
- Private helper functions starting with an underscore (such as `_receipt_identifies_host`) are internal implementation details and remain out of scope for the public API contract.

---

## Module: `fredlib.runs`

The `fredlib.runs` module manages the lifecycle of an audit run: planning skill investigations, initializing workspaces, generating prompts, validating ledgers, recording checkpoints, comparing runs, and reconciling remediation states.

### `now`

```python
def now() -> str
```

Returns the current UTC timestamp formatted as an ISO-8601 string.

- **Parameters:** None.
- **Returns:** `str` — An ISO-8601 UTC timestamp string (for example, `"2026-11-14T22:54:13.123456+00:00"`).
- **Exceptions:** None.

---

### `plan`

```python
def plan(
    repo: Path,
    task: str,
    mode: str = "investigate",
    budget: str = "balanced",
    base: str | None = None,
    profile: str | None = None,
    exclude: list[str] | None = None,
    config: Path | None = None,
) -> tuple[dict, dict]
```

Generates an execution plan for a specified task, balancing selected and deferred skills according to execution mode and budget constraints.

- **Parameters:**
  - `repo` (`Path`): Path to the target Git repository to audit.
  - `task` (`str`): Task recipe identifier matching a key in `catalog/recipes.json` (e.g., `"investigate-bug"`, `"review-pr"`).
  - `mode` (`str`, default: `"investigate"`): Execution mode. Allowed values: `"explore"`, `"changes"`, `"investigate"`, `"comprehensive"`.
  - `budget` (`str`, default: `"balanced"`): Work budget bounding skill selection. Allowed values: `"brief"` (up to 3 skills), `"balanced"` (up to 8 skills), `"deep"` (all selected skills).
  - `base` (`str | None`, default: `None`): Git revision for change reviews (required when `mode="changes"` or recipe sets `requires_base=true`).
  - `profile` (`str | None`, default: `None`): Optional profile name matching a key in `catalog/profiles.json`.
  - `exclude` (`list[str] | None`, default: `None`): Path patterns to exclude from inspection.
  - `config` (`Path | None`, default: `None`): Optional YAML configuration file path supporting `profile`, `exclude`, and `critical_workflows`.
- **Returns:** `tuple[dict, dict]` — A 2-tuple `(plan_record, preflight_record)`:
  - `plan_record` (`dict`): Contains `"task"`, `"mode"`, `"budget"`, `"profile"`, `"skills"`, `"deferred_skills"`, `"changed_files"`, `"base_revision"`, `"critical_workflows"`, `"project_config"`, `"expected_outputs"`, and `"notes"`.
  - `preflight_record` (`dict`): The environment preflight dictionary generated by `preflight(repo, exclude)`.
- **Exceptions:**
  - `ValueError`: Raised if `config` contains unsupported keys or malformed entries; if `task` is unknown; if `mode` or `budget` is invalid; if `base` is missing when required; or if `profile` is not in the profile catalog.

---

### `empty_audit`

```python
def empty_audit(run: dict) -> dict
```

Constructs a blank, schema 1.0 audit ledger bound to the given run metadata.

- **Parameters:**
  - `run` (`dict`): Run metadata dictionary created by `initialize()`.
- **Returns:** `dict` — A schema 1.0 audit dictionary containing:
  - `"schema_version"`: `"1.0"`
  - `"run_id"`: Matching `run["run_id"]`
  - `"repository"`: `{"identity": str, "revision": str, "dirty": bool, "snapshot": str}`
  - `"scope"`: Target workflows list
  - `"coverage"`: Initialized topic coverage records with status `"not-reviewed"`
  - `"contracts"`, `"evidence"`, `"findings"`, `"issues"`, `"checks"`: Empty lists
  - `"limitations"`: Initial limitation notice
- **Exceptions:** None.

---

### `initialize`

```python
def initialize(
    repo: Path,
    output: Path,
    task: str,
    allow_fixes: bool = False,
    allow_publication: bool = False,
    **options,
) -> dict
```

Initializes a new audit run workspace directory with `run.json`, `audit.json`, and `prompt.md`.

- **Parameters:**
  - `repo` (`Path`): Path to the target Git repository.
  - `output` (`Path`): Output directory where run artifacts are written. Must be a new or empty directory.
  - `task` (`str`): Task recipe identifier.
  - `allow_fixes` (`bool`, default: `False`): Explicit authority granting permission for application code changes.
  - `allow_publication` (`bool`, default: `False`): Explicit authority granting permission for remote issue publication.
  - `**options`: Additional keyword arguments forwarded to `plan()` (e.g., `mode`, `budget`, `base`, `profile`, `exclude`, `config`).
- **Returns:** `dict` — The initialized `run` dictionary written to `run.json`.
- **Exceptions:**
  - `ValueError`: Raised if `output` is the repository root or toolkit root, or if `output` already contains files; or if planning validation fails.

---

### `prompt`

```python
def prompt(run: dict) -> str
```

Formats the markdown instruction prompt for an agent or reviewer executing the planned run.

- **Parameters:**
  - `run` (`dict`): Run dictionary containing `"plan"`, `"repository"`, `"snapshot"`, and `"authority"`.
- **Returns:** `str` — Multi-line Markdown string detailing the task, repository snapshot, skill sequence, workflow bounds, and authority limits.
- **Exceptions:** None.

---

### `audit_errors`

```python
def audit_errors(audit: dict) -> list[str]
```

Validates an audit ledger against `schemas/audit.schema.json` and suite structural rules without executing network or subprocess calls.

- **Parameters:**
  - `audit` (`dict`): Audit ledger dictionary to validate.
- **Returns:** `list[str]` — A list of validation error descriptions (empty list if the audit ledger is valid).
- **Exceptions:** None.

---

### `validate_run`

```python
def validate_run(directory: Path) -> list[str]
```

Validates the integrity of an entire run directory, verifying `run.json` against `schemas/run.schema.json`, `audit.json` against the audit schema, snapshot hash integrity, matching run IDs, and snapshot consistency.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and `audit.json`.
- **Returns:** `list[str]` — List of validation failure messages (empty list if valid).
- **Exceptions:**
  - `OSError` / `FileNotFoundError`: Raised if run files cannot be read.

---

### `checkpoint`

```python
def checkpoint(directory: Path, state: str, note: str) -> dict
```

Appends a timestamped checkpoint record to `run.json` and updates the overall run state.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory.
  - `state` (`str`): New run state. Must be one of `"running"`, `"paused"`, or `"completed"`.
  - `note` (`str`): Non-empty explanation of continuation status, blockers, or completion notes.
- **Returns:** `dict` — The updated `run` record.
- **Exceptions:**
  - `ValueError`: Raised if `state` is not in `{"running", "paused", "completed"}` or `note` is empty or whitespace.

---

### `revalidate`

```python
def revalidate(directory: Path) -> dict
```

Compares the current repository state against the run's recorded snapshot, identifying changed files and stale audit findings.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and `audit.json`.
- **Returns:** `dict` — Revalidation record written to `revalidation.json`, containing `"old_snapshot"`, `"new_snapshot"`, `"changed_files"`, `"stale_finding_ids"`, `"unreviewed_changed_files"`, `"needs_impact_review"`, `"reusable_evidence_is_not_proof_of_unchanged_behavior"`, `"complete"`, and `"next_action"`.
- **Exceptions:** None.

---

### `fingerprint`

```python
def fingerprint(finding: dict, audit: dict) -> str
```

Calculates a deterministic SHA-256 fingerprint for a finding based on its topic, root cause, and resolved evidence locations.

- **Parameters:**
  - `finding` (`dict`): Finding dictionary from `audit["findings"]`.
  - `audit` (`dict`): Parent audit dictionary containing the referenced `"evidence"`.
- **Returns:** `str` — Hexadecimal SHA-256 digest string identifying the finding across runs.
- **Exceptions:** None.

---

### `compare`

```python
def compare(before: dict, after: dict) -> dict
```

Compares two audit ledgers using content fingerprints to track finding transitions without relying on volatile run-local IDs or line numbers.

- **Parameters:**
  - `before` (`dict`): Prior audit ledger dictionary.
  - `after` (`dict`): Current audit ledger dictionary.
- **Returns:** `dict` — Comparison summary containing:
  - `"introduced"`: `list[str]` — Finding IDs present in `after` but not `before`.
  - `"persisting"`: `list[dict]` — Pairs of `{"before": id, "after": id}` with identical status.
  - `"status_changed"`: `list[dict]` — Findings whose status changed between runs.
  - `"not_observed"`: `list[str]` — Finding IDs in `before` not observed in `after`.
  - `"ambiguous"`: `list[dict]` — Findings with colliding fingerprints.
  - `"limitations"`: `list[str]` — Warning notes on interpretation boundaries.
- **Exceptions:** None.

---

### `reconcile`

```python
def reconcile(
    directory: Path,
    finding_id: str,
    state: str,
    note: str,
    verification: Path | None = None,
) -> dict
```

Updates the remediation state of a finding in `directory / remediation.json`.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory.
  - `finding_id` (`str`): Target finding ID (must exist in `audit.json`).
  - `state` (`str`): New remediation state. Must be one of: `"open"`, `"in-progress"`, `"fixed-pending-verification"`, `"verified-fixed"`, `"regressed"`, `"accepted-risk"`.
  - `note` (`str`): Explanatory note for the transition.
  - `verification` (`Path | None`, default: `None`): Path to a structured verification proof artifact (mandatory when `state="verified-fixed"`).
- **Returns:** `dict` — The newly appended remediation record dictionary containing `"at"`, `"finding_id"`, `"fingerprint"`, `"state"`, `"note"`, and `"verification"`.
- **Exceptions:**
  - `ValueError`: Raised if `finding_id` is not in `audit.json`; if `state` is invalid; if `verification` is omitted when `state="verified-fixed"`; or if the verification artifact fails schema validation or execution assertions.

---

### `memory`

```python
def memory(directory: Path) -> dict
```

Generates a portable memory ledger extracting contracts, evidence hashes, and finding summaries from a completed run.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and `audit.json`.
- **Returns:** `dict` — Schema 1.0 memory dictionary containing `"schema_version"`, `"source_run"`, `"snapshot"`, `"contracts"`, `"evidence"`, `"findings"`, and `"limitations"`.
- **Exceptions:** None.

---

### `import_sarif`

```python
def import_sarif(path: Path) -> dict
```

Imports static analysis observations from a SARIF JSON file, normalizing file URIs and sanitizing relative paths.

- **Parameters:**
  - `path` (`Path`): Path to the SARIF file.
- **Returns:** `dict` — Schema 1.0 imported observation ledger containing:
  - `"schema_version"`: `"1.0"`
  - `"observations"`: List of normalized observation records (`tool`, `tool_version`, `rule`, `reported_level`, `message`, `locations`, `evidence_state`).
  - `"limitations"`: Explicit caveats indicating scanner claims require agent verification.
- **Exceptions:** None.

---

## Module: `fredlib.installation`

The `fredlib.installation` module handles skill discovery, host environment marker detection, directory resolution, atomic installation, rollback, removal, and journaled crash recovery.

### `detect_host`

```python
def detect_host(destination: Path, scope: str = "project") -> str
```

Inspects the target destination for agent configuration markers and historical receipts to identify the host platform automatically.

- **Parameters:**
  - `destination` (`Path`): Project root directory to inspect.
  - `scope` (`str`, default: `"project"`): Installation scope (`"project"` or `"user"`).
- **Returns:** `str` — Detected host identifier: `"claude-code"`, `"gemini-cli"`, `"antigravity"`, or `"codex"`.
- **Exceptions:**
  - `ValueError`: Raised if no host markers are found; if multiple conflicting marker directories exist; or if ambiguous receipt records exist for both codex and antigravity.

---

### `locations`

```python
def locations(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> tuple[Path, Path]
```

Resolves the target installation directory and internal state tracking directory for a specified host and scope.

- **Parameters:**
  - `destination` (`Path`): Target installation root path.
  - `host` (`str | None`, default: `None`): Host identifier matching `catalog/hosts.json` (auto-detected via `detect_host` if `None`).
  - `scope` (`str`, default: `"project"`): Installation scope (`"project"` or `"user"`).
  - `root` (`Path`, default: repository root): Toolkit root containing catalogs.
- **Returns:** `tuple[Path, Path]` — A 2-tuple `(target_path, state_path)`:
  - `target_path` (`Path`): Destination directory where skills are installed.
  - `state_path` (`Path`): Internal state directory under `.fred-skills/` where receipts, journals, and backups are kept.
- **Exceptions:**
  - `ValueError`: Raised if `host` is unknown, `scope` is invalid, or resolved paths escape `destination`.

---

### `lock`

```python
@contextmanager
def lock(state: Path) -> Generator[None, None, None]
```

Context manager that acquires an exclusive file lock (`operation.lock`) in the target state directory to prevent concurrent installations.

- **Parameters:**
  - `state` (`Path`): State directory path.
- **Yields:** `None`
- **Exceptions:**
  - `ValueError`: Raised if `operation.lock` already exists due to concurrent access or an interrupted operation.

---

### `receipt`

```python
def receipt(state: Path) -> dict
```

Reads the current installation receipt from `state / "receipt.json"`, or returns a fresh empty receipt structure if none exists.

- **Parameters:**
  - `state` (`Path`): State directory path.
- **Returns:** `dict` — Receipt dictionary with `"schema_version"`, `"skills"`, and `"history"`.
- **Exceptions:** None.

---

### `checked_target`

```python
def checked_target(target: Path, name: str) -> Path
```

Validates that a package target path is contained within the authorized destination directory and does not resolve through symlinks.

- **Parameters:**
  - `target` (`Path`): Authorized parent target directory.
  - `name` (`str`): Relative skill package name.
- **Returns:** `Path` — The resolved, validated package path.
- **Exceptions:**
  - `ValueError`: Raised if the candidate is a symlink or escapes `target`.

---

### `require_unmodified`

```python
def require_unmodified(path: Path, record: dict | None) -> None
```

Verifies that an existing on-disk skill package matches the recorded content hash from the installation receipt.

- **Parameters:**
  - `path` (`Path`): On-disk skill directory path.
  - `record` (`dict | None`): Receipt record for this skill containing `"content_hash"`, or `None` if unmanaged.
- **Returns:** `None`
- **Exceptions:**
  - `ValueError`: Raised if an unmanaged directory would be overwritten, if a managed skill was locally modified, or if a recorded skill is missing.

---

### `install`

```python
def install(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    pack: str | None = None,
    skills: list[str] | None = None,
    update: bool = False,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict
```

Installs or updates skills into the destination environment using a two-phase journaled staging and atomic swap workflow.

- **Parameters:**
  - `destination` (`Path`): Project or user root directory.
  - `host` (`str | None`, default: `None`): Host identifier (auto-detected if omitted).
  - `scope` (`str`, default: `"project"`): Installation scope (`"project"` or `"user"`).
  - `pack` (`str | None`, default: `None`): Named skill pack from `catalog/packs.json`.
  - `skills` (`list[str] | None`, default: `None`): Explicit list of skill names to install.
  - `update` (`bool`, default: `False`): Whether to allow replacing existing managed skills.
  - `dry_run` (`bool`, default: `False`): If `True`, returns planned operations without writing changes.
  - `root` (`Path`, default: repository root): Toolkit root directory.
- **Returns:** `dict` — Installation outcome summary with `"action"`, `"skills"`, `"host"`, `"scope"`, `"target"`, `"dry_run"`, `"source_version"`, and `"runtime_behavior_verified"`.
- **Exceptions:**
  - `ValueError`: Raised if host cannot be detected; if skills are already installed and `update=False`; if unmanaged or locally modified skills are encountered; or if lock cannot be acquired.

---

### `remove`

```python
def remove(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    skills: list[str] | None = None,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict
```

Removes managed skills from the destination environment, archiving previous packages to backups under the state directory.

- **Parameters:**
  - `destination` (`Path`): Installation destination root.
  - `host` (`str | None`, default: `None`): Host identifier (auto-detected if omitted).
  - `scope` (`str`, default: `"project"`): Installation scope.
  - `skills` (`list[str] | None`, default: `None`): List of skill names to remove.
  - `dry_run` (`bool`, default: `False`): If `True`, returns preview without removing packages.
  - `root` (`Path`, default: repository root): Toolkit root.
- **Returns:** `dict` — Removal outcome dictionary with `"action"`, `"skills"`, and `"dry_run"`.
- **Exceptions:**
  - `ValueError`: Raised if target skills are not managed by the receipt, or if skills were locally modified.

---

### `rollback`

```python
def rollback(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> dict
```

Reverts the most recent installation or removal operation using backup archives recorded in receipt history.

- **Parameters:**
  - `destination` (`Path`): Installation destination root.
  - `host` (`str | None`, default: `None`): Host identifier (auto-detected if omitted).
  - `scope` (`str`, default: `"project"`): Installation scope.
  - `root` (`Path`, default: repository root): Toolkit root.
- **Returns:** `dict` — Rollback summary with `"action"`, `"skills"`, and `"runtime_behavior_verified"`.
- **Exceptions:**
  - `ValueError`: Raised if receipt history is empty; if the receipt changed since the operation; or if backup files are missing or modified.

---

### `recover`

```python
def recover(
    destination: Path,
    host: str | None = None,
    scope: str = "project",
    confirm_interrupted: bool = False,
    root: Path = ROOT,
) -> dict
```

Recovers a partially completed or interrupted installation using the state journal (`operation.json`).

- **Parameters:**
  - `destination` (`Path`): Installation destination root.
  - `host` (`str | None`, default: `None`): Host identifier (auto-detected if omitted).
  - `scope` (`str`, default: `"project"`): Installation scope.
  - `confirm_interrupted` (`bool`, default: `False`): Required confirmation flag confirming the previous writer process has halted.
  - `root` (`Path`, default: repository root): Toolkit root.
- **Returns:** `dict` — Recovery outcome dictionary:
  - If already completed: `{"recovery": "already-committed", "skills": list[str]}`
  - If restored: `{"recovery": "restored-before-state", "skills": list[str], "preserved_interrupted_content": str}`
- **Exceptions:**
  - `ValueError`: Raised if `confirm_interrupted` is `False`; if the journal and receipt disagree; if required backups are missing; or if destination files have been tampered with.

---

## Module: `fredlib.evaluations`

The `fredlib.evaluations` module executes test harness evaluations for agent adapters against synthetic fixtures without shell invocation.

### `evaluate`

```python
def evaluate(
    output: Path,
    command: list[str] | None = None,
    cases: list[str] | None = None,
    repeats: int = 1,
    timeout: int = 120,
    root: Path = ROOT,
) -> dict
```

Runs a user-supplied adapter executable over evaluation test cases, executing both baseline (`without_skill`) and skill-assisted (`with_skill`) configurations, streaming stdout/stderr to disk logs, and grading output against expected rubrics.

- **Parameters:**
  - `output` (`Path`): Output directory for evaluation logs and `benchmark.json`. Must be a new or empty directory.
  - `command` (`list[str] | None`, default: `None`): Command argv list for the adapter process. Supports placeholders `{prompt_file}`, `{output_file}`, and `{case_dir}`.
  - `cases` (`list[str] | None`, default: `None`): Optional subset of case IDs from `evals/cases.json` to evaluate.
  - `repeats` (`int`, default: `1`): Number of repetitions per case and configuration (bounded between 1 and 10).
  - `timeout` (`int`, default: `120`): Per-execution timeout limit in seconds (bounded between 1 and 3600).
  - `root` (`Path`, default: repository root): Toolkit root containing evaluation cases and skill packages.
- **Returns:** `dict` — Benchmark results dictionary containing:
  - `"schema_version"`: `"1.0"`
  - `"toolkit_version"`: Version string
  - `"adapter_argv"`: Command arguments used
  - `"cases_digest"`: SHA-256 digest of cases definition
  - `"runs"`: List of individual run grading records
  - `"summary"`: Statistical aggregate by configuration (`"runs"`, `"exact_match_rate"`, `"mean_seconds"`, `"failures"`)
  - `"limitations"`: Evaluation boundary notices
- **Exceptions:**
  - `ValueError`: Raised if `command` is missing or empty; if `repeats` or `timeout` is outside valid ranges; if `output` exists and is non-empty; or if unknown case IDs are specified.

---

## Module: `fredlib.reports`

The `fredlib.reports` module renders self-contained interactive HTML audit reports and generates local markdown or JSON issue drafts.

### `text`

```python
def text(value) -> str
```

Escapes a value into an HTML-safe string with double and single quotes escaped.

- **Parameters:**
  - `value`: Any value to convert to string and escape.
- **Returns:** `str` — Escaped HTML string safe for inline embedding.
- **Exceptions:** None.

---

### `render`

```python
def render(audit: dict, output: Path, prior: dict | None = None) -> None
```

Renders a standalone, single-file interactive HTML report from an audit ledger and optional prior audit ledger for diff comparison.

- **Parameters:**
  - `audit` (`dict`): Current audit ledger dictionary.
  - `output` (`Path`): Output file path for the generated HTML report.
  - `prior` (`dict | None`, default: `None`): Prior audit ledger dictionary to render comparison sections.
- **Returns:** `None`
- **Exceptions:**
  - `ValueError`: Raised if `audit` fails schema or consistency validation.

---

### `issue_drafts`

```python
def issue_drafts(audit: dict, output: Path, format: str = "markdown") -> list[str]
```

Extracts draft issue records from an audit ledger and writes local issue files for review.

- **Parameters:**
  - `audit` (`dict`): Validated audit ledger containing `"issues"` and `"findings"`.
  - `output` (`Path`): Target directory for draft files. Must be empty.
  - `format` (`str`, default: `"markdown"`): Output file format. Allowed values: `"markdown"` (creates `.md` files) or `"json"` (creates GitHub API compatible `.json` files).
- **Returns:** `list[str]` — List of string paths for the created draft issue files.
- **Exceptions:**
  - `ValueError`: Raised if `format` is not `"markdown"` or `"json"`; if `audit` fails validation; if `output` already contains files; or if an issue ID contains filename-unsafe characters.

---

## Module: `fredlib.releases`

The `fredlib.releases` module packages skills into reproducible `.zip` and `.tar` archives and verifies package integrity against release manifests.

### `build`

```python
def build(
    output: Path,
    pack: str | None = None,
    skills: list[str] | None = None,
    root: Path = ROOT,
) -> dict
```

Builds reproducible release distribution archives containing selected standalone skills with normalized timestamps and POSIX permissions.

- **Parameters:**
  - `output` (`Path`): Output directory for archives, `manifest.json`, and `SHA256SUMS`. Must be new or empty.
  - `pack` (`str | None`, default: `None`): Named pack identifier from `catalog/packs.json`.
  - `skills` (`list[str] | None`, default: `None`): Explicit skill names list.
  - `root` (`Path`, default: repository root): Toolkit root directory.
- **Returns:** `dict` — Manifest dictionary containing `"schema_version"`, `"version"`, `"pack"`, `"skills"`, `"licensing"`, and `"archives"` (listing filenames, byte counts, and SHA-256 hashes).
- **Exceptions:**
  - `ValueError`: Raised if `output` exists and is non-empty, or if pack label contains invalid characters.

---

### `verify`

```python
def verify(manifest: Path) -> dict
```

Validates the integrity of release archives against a committed or generated `manifest.json`.

- **Parameters:**
  - `manifest` (`Path`): Path to `manifest.json`.
- **Returns:** `dict` — Verification summary dictionary containing `"verified_archives"`, `"version"`, and `"authenticity"`.
- **Exceptions:**
  - `ValueError`: Raised if an archive path escapes the manifest directory, or if an archive file's SHA-256 checksum does not match the manifest.

---

## Module: `fredlib.repository`

The `fredlib.repository` module provides Git subprocess execution, repository file snapshots, diff path resolution, and read-only preflight environment inspection.

### `git`

```python
def git(repo: Path, *args: str, required: bool = False) -> str | None
```

Executes a Git command in the target repository directory with `core.fsmonitor=false` and a 30-second timeout.

- **Parameters:**
  - `repo` (`Path`): Repository directory path.
  - `*args` (`str`): Git command-line arguments (for example, `"rev-parse"`, `"HEAD"`).
  - `required` (`bool`, default: `False`): If `True`, raises `ValueError` on command failure or missing binary.
- **Returns:** `str | None` — Stripped standard output string, or `None` if Git fails or is not found when `required=False`.
- **Exceptions:**
  - `ValueError`: Raised when `required=True` and Git is not installed or returns a non-zero exit code.

---

### `snapshot`

```python
def snapshot(
    repo: Path,
    exclude: list[str] | None = None,
    max_files: int = 10000,
    max_bytes: int = 5_000_000,
) -> dict
```

Takes a deterministic content snapshot of all tracked and untracked files in a repository, computing individual and aggregate SHA-256 digests.

- **Parameters:**
  - `repo` (`Path`): Target repository path.
  - `exclude` (`list[str] | None`, default: `None`): Glob patterns to exclude.
  - `max_files` (`int`, default: `10000`): Maximum number of files to index before stopping.
  - `max_bytes` (`int`, default: `5000000`): Maximum file size in bytes for hashing; larger files are flagged as `size-limit`.
- **Returns:** `dict` — Snapshot dictionary containing:
  - `"digest"`: Aggregate SHA-256 digest string identifying the overall content state
  - `"files"`: Dictionary mapping relative POSIX file paths to their SHA-256 digests
  - `"skipped"`: Dictionary mapping skipped paths to exclusion reasons
  - `"exclude"`: Normalized exclude patterns
  - `"complete"`: `bool` indicating whether all files were hashed without truncation
  - `"max_files"`, `"max_file_bytes"`: Enforced limits
- **Exceptions:**
  - `ValueError`: Raised if `repo` directory does not exist.

---

### `changed_files`

```python
def changed_files(repo: Path, base: str) -> tuple[list[str], str]
```

Resolves changed file paths between a base commit revision and the working tree, including committed differences, dirty tracked modifications, and untracked files.

- **Parameters:**
  - `repo` (`Path`): Repository directory path.
  - `base` (`str`): Git revision expression (branch, tag, or commit SHA).
- **Returns:** `tuple[list[str], str]` — A 2-tuple `(changed_paths, merge_base_sha)`:
  - `changed_paths` (`list[str]`): Sorted list of relative POSIX paths modified or added since the merge base.
  - `merge_base_sha` (`str`): The resolved merge-base commit SHA.
- **Exceptions:**
  - `ValueError`: Raised if `base` cannot be resolved to a commit or if Git commands fail.

---

### `preflight`

```python
def preflight(repo: Path, exclude: list[str] | None = None) -> dict
```

Performs read-only inspection of the repository checkout and host environment to detect programming languages, repository revision, and binary tool availability.

- **Parameters:**
  - `repo` (`Path`): Target repository path.
  - `exclude` (`list[str] | None`, default: `None`): Glob patterns to exclude from file inventory.
- **Returns:** `dict` — Preflight inspection dictionary containing:
  - `"toolkit_version"`: Version string
  - `"python_version"`: Active Python runtime version
  - `"repository"`: `{"path": str, "revision": str, "dirty": bool}`
  - `"snapshot"`: Snapshot dictionary returned by `snapshot()`
  - `"languages"`: Sorted list of detected programming languages (e.g., `["Python", "TypeScript"]`)
  - `"capabilities"`: Map of detected binaries and environmental features (`"source_read"`, `"git"`, `"tool_binaries"`, `"agent_binaries"`)
  - `"limitations"`: Explicit boundary notices
- **Exceptions:** None (returns gracefully even if Git is absent).
