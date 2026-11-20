# Developer API Guide

This document provides the authoritative programmatic API reference for the seven library modules in `scripts/fredlib/`. These modules provide reusable, scriptable components for run lifecycle management, skill installation, evaluation benchmarking, HTML/draft reporting, release packaging, repository inspection, and offline security vetting.

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
   - [`advance`](#advance)
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
   - [`query_installed`](#query_installed)
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
8. [Module: `fredlib.vetting`](#module-fredlibvetting)
   - [`vet_script`](#vet_script)
   - [`vet_markdown`](#vet_markdown)
   - [`vet_mcp_config`](#vet_mcp_config)
   - [`vet_skill`](#vet_skill)
   - [`vet_target`](#vet_target)

---

## Overview and Import Conventions

The toolkit's programmatic package is located at `scripts/fredlib`. Scripts can import either individual modules or the package root:

```python
import sys
from pathlib import Path

# Ensure the scripts directory is on sys.path
sys.path.insert(0, str(Path("/path/to/fred-skills/scripts")))

from fredlib import (
    evaluations,
    installation,
    releases,
    reports,
    repository,
    runs,
    vetting,
)
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
    directory: str | None = None,
    symbol: str | None = None,
    allowed_paths: list[str] | None = None,
    do_not_modify_paths: list[str] | None = None,
    issue_number: int | None = None,
) -> tuple[dict, dict]
```

Generates an execution plan for a specified task, balancing selected and deferred skills according to execution mode, budget, and optional tactical scope constraints.

- **Parameters:**
  - `repo` (`Path`): Path to the target Git repository to audit.
  - `task` (`str`): Task recipe identifier matching a key in `catalog/recipes.json` (e.g., `"investigate-bug"`, `"review-pr"`).
  - `mode` (`str`, default: `"investigate"`): Execution mode. Allowed values: `"explore"`, `"changes"`, `"investigate"`, `"comprehensive"`.
  - `budget` (`str`, default: `"balanced"`): Work budget bounding skill selection. Allowed values: `"brief"` (up to 3 skills), `"balanced"` (up to 8 skills), `"deep"` (all selected skills).
  - `base` (`str | None`, default: `None`): Git revision for change reviews (required when `mode="changes"` or recipe sets `requires_base=true`).
  - `profile` (`str | None`, default: `None`): Optional profile name matching a key in `catalog/profiles.json`.
  - `exclude` (`list[str] | None`, default: `None`): Path patterns to exclude from inspection.
  - `config` (`Path | None`, default: `None`): Optional YAML configuration file path supporting `profile`, `exclude`, and `critical_workflows`.
  - `directory` (`str | None`, default: `None`): Optional relative directory path within `repo` to focus tactical scope and directory mapping.
  - `symbol` (`str | None`, default: `None`): Optional target symbol, function, or type name for blast-radius tracing and caller migration ordering.
  - `allowed_paths` (`list[str] | None`, default: `None`): Optional relative paths or glob patterns permitted for edits when `allow_fixes=True`.
  - `do_not_modify_paths` (`list[str] | None`, default: `None`): Optional relative paths or glob patterns that must never be modified (taking precedence over `allowed_paths`).
  - `issue_number` (`int | None`, default: `None`): Optional positive GitHub issue number for read-only `gh` CLI context queries.
- **Returns:** `tuple[dict, dict]` — A 2-tuple `(plan_record, preflight_record)`:
  - `plan_record` (`dict`): Contains `"task"`, `"mode"`, `"budget"`, `"profile"`, `"skills"`, `"deferred_skills"`, `"changed_files"`, `"base_revision"`, `"critical_workflows"`, `"project_config"`, `"expected_outputs"`, `"directory"`, `"symbol"`, `"allowed_paths"`, `"do_not_modify_paths"`, `"issue_number"`, and `"notes"`.
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
    single_task: bool = False,
    source_audit: Path | None = None,
    **options,
) -> dict
```

Initializes a new run workspace directory with `run.json`, a single-slice `prompt.md` (bounded to at most 3 active skills), and the active suite's primary artifact (`audit.json`, `architecture.json`, `review.json`, `testing.json`, or `spec.json` declared in `catalog/suites.json`).

- **Parameters:**
  - `repo` (`Path`): Path to the target Git repository.
  - `output` (`Path`): Output directory where run artifacts are written. Must be a new or empty directory.
  - `task` (`str`): Task recipe identifier.
  - `allow_fixes` (`bool`, default: `False`): Explicit authority granting permission for application code changes.
  - `allow_publication` (`bool`, default: `False`): Explicit authority granting permission for remote issue publication.
  - `single_task` (`bool`, default: `False`): When `True`, initializes `run["loop"]` with an ordered per-skill queue (`active_index=0`, empty `snapshot_history`) and emits only the active pass's skill in `prompt.md`.
  - `source_audit` (`Path | None`, default: `None`): Optional path to an existing `audit.json` file or audit run directory used to seed confirmed findings and evidence into a workflow/audit workspace without modifying the source ledger.
  - `**options`: Additional keyword arguments forwarded to `plan()` (e.g., `mode`, `budget`, `base`, `profile`, `exclude`, `config`).
- **Returns:** `dict` — The initialized `run` dictionary written to `run.json`.
- **Exceptions:**
  - `ValueError`: Raised if `single_task` is not a boolean; if `output` is the repository root or toolkit root, or if `output` already contains files; or if planning validation fails.

---

### `prompt`

```python
def prompt(run: dict) -> str
```

Formats the composed tactical markdown instruction prompt for an agent or reviewer executing the planned run, emitting a stateless phase-slice brief (at most 3 active skills per session) by interpolating only the active slice's `skills/*/assets/prompt.md` templates alongside declared dependency versions, a read-only directory map, symbol blast-radius migration ordering, verified facts versus assumptions, path guards, and read-only `gh` CLI queries. When `run["loop"]["single_task"]` is `True`, emits only the single active pass's skill instructions and companion deliverable contracts (or a completion notice once all passes finish).

- **Parameters:**
  - `run` (`dict`): Run dictionary containing `"plan"`, `"repository"`, `"snapshot"`, `"authority"`, and optional `"dependencies"` / `"capabilities"`.
- **Returns:** `str` — Multi-line Markdown string detailing the task, repository snapshot, declared dependencies, interpolated skill instructions, tactical scope guards, and authority limits.
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

Validates the integrity of an entire run directory, verifying `run.json` against `schemas/run.schema.json`, snapshot hash integrity, optional `loop` task-queue invariants, present `repository-workflows` phase deliverables against their standalone skill schemas, and the suite deliverable (`audit.json`, `architecture.json`, `review.json`, `testing.json`, or `spec.json`) against its declarative suite schema (plus matching `run_id` and snapshot provenance for `audit.json`, `architecture.json`, `review.json`, and `testing.json`, accepting prior digests recorded in `loop["snapshot_history"]`).

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and the suite deliverable.
- **Returns:** `list[str]` — List of validation failure messages (empty list if valid).
- **Exceptions:**
  - `OSError` / `FileNotFoundError`: Raised if run files cannot be read.

---

### `checkpoint`

```python
def checkpoint(
    directory: Path,
    state: str,
    note: str,
    completed_skills: list[str] | None = None,
) -> dict
```

Appends a timestamped checkpoint record (including `completed_skills`, `active_phase`, and `remaining_skills` cursor progress) to `run.json` and updates the overall run state.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory.
  - `state` (`str`): New run state. Must be one of `"running"`, `"paused"`, or `"completed"`.
  - `note` (`str`): Non-empty explanation of continuation status, blockers, or completion notes.
  - `completed_skills` (`list[str] | None`, default: `None`): Optional planned skill names completed in the current session slice, merged with prior checkpoint progress.
- **Returns:** `dict` — The updated `run` record.
- **Exceptions:**
  - `ValueError`: Raised if `state` is not in `{"running", "paused", "completed"}` or `note` is empty or whitespace.

---

### `revalidate`

```python
def revalidate(directory: Path) -> dict
```

Compares the current repository state against the run's recorded snapshot, validating the active suite deliverable (`audit.json`, `architecture.json`, `review.json`, `testing.json`, or `spec.json`), computing changed files and stale or impacted records across cited `paths`/`file` references, and advancing `prompt.md` to target only the next uncompleted phase slice without mutating historical audit evidence.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and the suite deliverable.
- **Returns:** `dict` — Revalidation record written to `revalidation.json`, containing `"run_id"`, `"deliverable"`, `"old_snapshot"`, `"new_snapshot"`, `"changed_files"`, `"stale_finding_ids"`, `"unreviewed_changed_files"`, `"needs_impact_review"`, `"provenance_valid"`, `"active_phase"`, `"active_skills"`, `"completed_skills"`, `"remaining_skills"`, `"reusable_evidence_is_not_proof_of_unchanged_behavior"`, `"complete"`, and `"next_action"`.
- **Exceptions:**
  - `ValueError`: Raised if the suite deliverable is missing or fails schema validation.

---

### `advance`

```python
def advance(directory: Path, note: str | None = None) -> dict
```

Checks repository freshness via `revalidate()`, appends the completed pass's snapshot digest and metadata to `loop["snapshot_history"]`, updates `run.json` snapshot and repository state to the current checkout, advances the loop task queue to the next planned skill, and regenerates `prompt.md` for a fresh context window.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and the suite deliverable.
  - `note` (`str | None`, default: `None`): Optional single-line note recorded on the completed loop task entry.
- **Returns:** `dict` — Progression summary containing `"run_id"`, `"state"`, `"pass_index"`, `"total_passes"`, `"completed_skill"`, `"active_skill"`, `"completed"`, `"old_snapshot"`, `"new_snapshot"`, `"changed_files"`, `"stale_finding_ids"`, `"prompt"`, and `"revalidation"`.
- **Exceptions:**
  - `ValueError`: Raised if `note` is empty or multi-line; if `validate_run(directory)` reports errors before advancing; or if all planned loop tasks are already completed.

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

Generates a portable cross-run memory ledger from a validated run workspace across `audit.json`, `architecture.json`, `review.json`, `testing.json`, or `spec.json` with SHA-256 file provenance.

- **Parameters:**
  - `directory` (`Path`): Path to the run directory containing `run.json` and its suite deliverable.
- **Returns:** `dict` — Schema 1.0 memory dictionary:
  - For `audit.json`: `"schema_version"`, `"source_run"`, `"snapshot"`, `"contracts"`, `"evidence"` (with `"content_sha256"`), `"findings"`, and `"limitations"`.
  - For domain deliverables (`architecture.json`, `review.json`, `testing.json`, `spec.json`): `"schema_version"`, `"source_run"`, `"suite"`, `"deliverable"`, `"snapshot"`, `"file_hashes"`, domain records annotated with `"content_sha256"` / `"referenced_file_hashes"`, and `"limitations"`.
- **Exceptions:**
  - `ValueError`: Raised if `directory`, `run.json`, or the expected suite deliverable is missing, or if schema or snapshot integrity validation fails.

---

### `import_sarif`

```python
def import_sarif(path: Path, root: Path | None = None) -> dict
```

Imports static analysis observations from a SARIF JSON file, normalizing `file://` URIs against SARIF base metadata or an optional repository root, inheriting rule severity levels, and sanitizing relative paths.

- **Parameters:**
  - `path` (`Path`): Path to the SARIF file.
  - `root` (`Path | None`, default: `None`): Optional repository root directory used to resolve `file://` URIs and reject out-of-tree paths.
- **Returns:** `dict` — Schema 1.0 imported observation ledger containing:
  - `"schema_version"`: `"1.0"`
  - `"observations"`: List of normalized observation records (`tool`, `tool_version`, `rule`, `reported_level`, `message`, `locations`, `evidence_state`, plus optional `security_severity`, `fingerprints`, and `partial_fingerprints`).
  - `"limitations"`: Explicit caveats indicating scanner claims require agent verification.
- **Exceptions:** None.

---

## Module: `fredlib.installation`

The `fredlib.installation` module handles skill discovery, host environment marker detection, directory resolution, atomic installation, rollback, removal, and journaled crash recovery.

### `detect_host`

```python
def detect_host(destination: Path | None = None, scope: str = "project") -> str
```

Inspects the target destination for declarative `project_markers` or `user_markers` configured in `catalog/hosts.json` (`primary` before `fallback`) and historical receipts to identify the host platform automatically.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Project root or user home directory to inspect, or `None` to default to current working directory (`"project"` scope) or user home (`"user"` scope).
  - `scope` (`str`, default: `"project"`): Installation scope (`"project"` or `"user"`).
- **Returns:** `str` — One of the nine host identifiers from `catalog/hosts.json`: `"codex"`, `"claude-code"`, `"antigravity"`, `"gemini-cli"`, `"cursor"`, `"copilot"`, `"opencode"`, `"windsurf"`, or `"kiro"`.
- **Exceptions:**
  - `ValueError`: Raised if `scope` is invalid; if no configured host markers are found; if markers from multiple distinct hosts coexist; or if conflicting receipt records exist alongside shared or fallback markers.

---

### `locations`

```python
def locations(
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> tuple[Path, Path]
```

Resolves the target installation directory and internal state tracking directory for a specified host and scope.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Target installation root path, or `None` to default to current working directory (project scope) or user home (user scope).
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

### `query_installed`

```python
def query_installed(
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> dict
```

Reads installation receipts and resolves host harness target paths for a target destination, aggregating across all detected hosts when `host` is omitted on multi-host workspaces.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Target installation root path, or `None` to default to current working directory (project scope) or user home (user scope).
  - `host` (`str | None`, default: `None`): Agent host identifier, or `None` to auto-detect all configured workspace hosts.
  - `scope` (`str`, default: `"project"`): Target scope (`"project"` or `"user"`).
  - `root` (`Path`, default: repository root): Toolkit root directory.
- **Returns:** `dict` — Installation status dictionary containing `"destination"`, `"target"`, `"state"`, `"host"`, `"scope"`, `"skills"` (each entry enriched with `"status"` as `"tracked"`, `"modified"`, or `"missing"`), `"untracked"` (`list[str]` of unmanaged `SKILL.md` directories), `"locked"` (`bool`), and `"healthy"` (`bool`), plus `"hosts"` (`list[dict]` of per-host status records) when multiple hosts are detected.
- **Exceptions:**
  - `ValueError`: Raised if conflicting receipts claim the same shared target directory without an explicit `host` parameter, or if `scope` or `host` is unknown.

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
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    pack: str | None = None,
    skills: list[str] | None = None,
    update: bool = False,
    dry_run: bool = False,
    force: bool = False,
    root: Path = ROOT,
) -> dict
```

Installs or updates skills into the destination environment using a two-phase journaled staging and atomic swap workflow, fanning out atomically across detected workspace hosts when `host` is omitted.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Project or user root directory, or `None` to default to current working directory (project scope) or user home (user scope).
  - `host` (`str | None`, default: `None`): Host identifier (when omitted, fans out across detected workspace hosts with shared `.agents/skills` target deduplication, or bootstraps `codex` when zero markers exist on `install`).
  - `scope` (`str`, default: `"project"`): Installation scope (`"project"` or `"user"`).
  - `pack` (`str | None`, default: `None`): Named skill pack from `catalog/packs.json` (defaults to `"starter"` when both `pack` and `skills` are omitted on `install`).
  - `skills` (`list[str] | None`, default: `None`): Explicit list of skill names to install.
  - `update` (`bool`, default: `False`): Whether to allow replacing existing managed skills.
  - `dry_run` (`bool`, default: `False`): If `True`, returns planned operations without writing changes.
  - `force` (`bool`, default: `False`): If `True`, archives modified managed folders under `.fred-skills/<host>-<scope>/backups/` and repairs modified or missing managed skills without touching `untracked` user directories.
  - `root` (`Path`, default: repository root): Toolkit root directory.
- **Returns:** `dict` — Installation outcome summary with `"action"`, `"skills"`, `"host"`, `"scope"`, `"target"`, `"dry_run"`, `"source_version"`, and `"runtime_behavior_verified"`, plus `"hosts"` (`list[dict]`) when fanning out across multiple detected hosts.
- **Exceptions:**
  - `ValueError`: Raised if `update=True` finds no detected host markers or receipts; if conflicting receipts claim the same shared target directory; if skills are already installed and both `update=False` and `force=False`; if unmanaged skills are encountered; if locally modified or missing managed skills are encountered with `force=False`; or if lock cannot be acquired.

---

### `remove`

```python
def remove(
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    skills: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
    root: Path = ROOT,
) -> dict
```

Removes managed skills from the destination environment, archiving previous packages to backups under the state directory.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Installation destination root, or `None` to default to current working directory (project scope) or user home (user scope).
  - `host` (`str | None`, default: `None`): Host identifier (auto-detected if omitted).
  - `scope` (`str`, default: `"project"`): Installation scope.
  - `skills` (`list[str] | None`, default: `None`): List of skill names to remove.
  - `dry_run` (`bool`, default: `False`): If `True`, returns preview without removing packages.
  - `force` (`bool`, default: `False`): If `True`, archives any remaining modified managed folders to backups and removes modified or missing managed skills from `receipt.json`.
  - `root` (`Path`, default: repository root): Toolkit root.
- **Returns:** `dict` — Removal outcome dictionary with `"action"`, `"skills"`, and `"dry_run"`.
- **Exceptions:**
  - `ValueError`: Raised if multiple host receipts or markers exist without an explicit `host` parameter, if target skills are not managed by the receipt, or if managed skills were locally modified or missing with `force=False`.

---

### `rollback`

```python
def rollback(
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    root: Path = ROOT,
) -> dict
```

Reverts the most recent installation or removal operation using backup archives recorded in receipt history.

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Installation destination root, or `None` to default to current working directory (project scope) or user home (user scope).
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
    destination: Path | None = None,
    host: str | None = None,
    scope: str = "project",
    confirm_interrupted: bool = False,
    root: Path = ROOT,
) -> dict
```

Recovers a partially completed or interrupted installation using the state journal (`operation.json`).

- **Parameters:**
  - `destination` (`Path | None`, default: `None`): Installation destination root, or `None` to default to current working directory (project scope) or user home (user scope).
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
    max_tokens: int | None = None,
) -> dict
```

Runs a user-supplied adapter executable over blind evaluation test cases across all six suites (`audit`, `architecture`, `review`, `spec`, `testing`, and `workflow` contracts), executing both baseline (`without_skill`) and skill-assisted (`with_skill`) configurations, streaming stdout/stderr to disk logs, validating deliverables against canonical suite and workflow schemas plus all six semantic suite validators (`validate_audit`, `validate_architecture`, `validate_review`, `validate_spec`, `validate_testing`, and `validate_workflow`), enforcing read-only `./source` isolation unless matched by declared `writable_paths` globs, enforcing optional `max_tokens` budgets, and grading outputs against structural property anchors.

- **Parameters:**
  - `output` (`Path`): Output directory for evaluation logs and `benchmark.json`. Must be a new or empty directory.
  - `command` (`list[str] | None`, default: `None`): Command argv list for the adapter process. Supports placeholders `{prompt_file}`, `{output_file}`, and `{case_dir}`.
  - `cases` (`list[str] | None`, default: `None`): Optional subset of case IDs from `evals/cases.json` to evaluate.
  - `repeats` (`int`, default: `1`): Number of repetitions per case and configuration (bounded between 1 and 10).
  - `timeout` (`int`, default: `120`): Per-execution timeout limit in seconds (bounded between 1 and 3600).
  - `root` (`Path`, default: repository root): Toolkit root containing evaluation cases and skill packages.
  - `max_tokens` (`int | None`, default: `None`): Optional positive per-run token cap; runs reporting `usage.tokens` above `max_tokens` fail `exact_match` with a token budget error.
- **Returns:** `dict` — Benchmark results dictionary containing:
  - `"schema_version"`: `"1.0"`
  - `"toolkit_version"`: Version string
  - `"adapter_argv"`: Command arguments used
  - `"cases_digest"`: SHA-256 digest of cases definition
  - `"runs"`: List of individual run grading records
  - `"summary"`: Statistical aggregate by configuration (`"runs"`, `"exact_match_rate"`, `"mean_seconds"`, `"failures"`, `"tokens_reported_runs"`, `"total_tokens"`, `"mean_tokens"`) plus paired `"skill_lift"` deltas across `without_skill` and `with_skill` (`"paired_runs"`, `"exact_match_rate_delta"`, `"mean_seconds_delta"`, `"failures_delta"`, `"true_positives_delta"`, `"false_positives_delta"`, `"false_negatives_delta"`, `"paired_token_runs"`, `"total_tokens_delta"`, `"mean_tokens_delta"`, preserving `None` when token usage is omitted)
  - `"limitations"`: Evaluation boundary notices
- **Exceptions:**
  - `ValueError`: Raised if `command` is missing or empty; if `repeats`, `timeout`, or `max_tokens` is outside valid ranges; if `output` exists and is non-empty; or if unknown case IDs are specified.

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
def render(
    audit: dict,
    output: Path,
    prior: dict | None = None,
    remediation: dict | None = None,
) -> Path
```

Renders a standalone, single-file interactive HTML report from an audit ledger, optional prior audit ledger for structured diff comparison tables, and optional remediation ledger for reconciled fix states.

- **Parameters:**
  - `audit` (`dict`): Current audit ledger dictionary (or domain deliverable dictionary).
  - `output` (`Path`): Output directory or file path for the generated HTML report.
  - `prior` (`dict | None`, default: `None`): Prior audit ledger dictionary to render structured comparison tables.
  - `remediation` (`dict | None`, default: `None`): Optional `remediation.json` dictionary containing reconciled finding state and note records, rendered separately from evidence certainty.
- **Returns:** `Path` — Resolved file path of the written HTML report.
- **Exceptions:**
  - `ValueError`: Raised if `audit` or `remediation` fails schema or consistency validation, or if `prior` or `remediation` is supplied for a non-audit domain artifact.

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
    all_packs: bool = False,
) -> dict
```

Builds reproducible release distribution archives containing selected standalone skills with normalized timestamps and POSIX permissions.

- **Parameters:**
  - `output` (`Path`): Output directory for archives, `manifest.json`, and `SHA256SUMS`. Must be new or empty.
  - `pack` (`str | None`, default: `None`): Named pack identifier from `catalog/packs.json`.
  - `skills` (`list[str] | None`, default: `None`): Explicit skill names list.
  - `root` (`Path`, default: repository root): Toolkit root directory.
  - `all_packs` (`bool`, default: `False`): If `True`, package all documented packs, standalone skill archives, and unified manifest.
- **Returns:** `dict` — Manifest dictionary containing `"schema_version"`, `"version"`, `"pack"`, `"skills"`, `"licensing"`, and `"archives"` (listing filenames, byte counts, SHA-256 hashes, and included `"skills"`).
- **Exceptions:**
  - `ValueError`: Raised if `output` exists and is non-empty, or if pack label contains invalid characters.

---

### `verify`

```python
def verify(manifest: Path) -> dict
```

Validates the integrity of release archives (and adjacent `SHA256SUMS` and `checksums.sha256` files when present) against a committed or generated `manifest.json`.

- **Parameters:**
  - `manifest` (`Path`): Path to `manifest.json`.
- **Returns:** `dict` — Verification summary dictionary containing `"verified_archives"`, `"version"`, and `"authenticity"`.
- **Exceptions:**
  - `ValueError`: Raised if an archive path escapes the manifest directory or is missing; if an archive's byte count or SHA-256 checksum does not match `manifest.json`; or if a present `SHA256SUMS` or `checksums.sha256` file is a symlink, contains malformed lines, or disagrees with `manifest.json`.

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
  - `required` (`bool`, default: `False`): If `True`, raises `ValueError` on command failure, timeout, or missing binary.
- **Returns:** `str | None` — Stripped standard output string, or `None` if Git fails, times out, or is not found when `required=False`.
- **Exceptions:**
  - `ValueError`: Raised when `required=True` and Git is not installed, times out, or returns a non-zero exit code.

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
  - `"dependencies"`: Offline manifest parse record (`{"manifests": list[dict], "packages": dict[str, str], "errors": dict[str, str]}`) extracted from `package.json`, `pyproject.toml`, `Cargo.toml`, and `go.mod`
  - `"capabilities"`: Map of detected binaries and environmental features (`"source_read"`, `"git"`, `"gh"`, `"tool_binaries"`, `"agent_binaries"`)
  - `"limitations"`: Explicit boundary notices
- **Exceptions:** None (returns gracefully even if Git is absent).

---

## Module: `fredlib.vetting`

The `fredlib.vetting` module runs offline static AST and regex security vetting on skill scripts, Markdown prompts, MCP server configurations, standalone skill packages, and repository agent configurations using only the Python 3.12 standard library and zero network requests.

### `vet_script`

```python
def vet_script(
    path: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]
```

Inspects a Python, JS/TS, or shell script offline for network clients, process/shell execution, dynamic loaders (`eval`/`exec`/`importlib`/`runpy`), reflective execution/network lookups (`getattr`/`operator.attrgetter`), hidden Unicode controls, and undeclared credential, `Path.joinpath`, or bulk `os.environ` reads.

- **Parameters:**
  - `path` (`Path`): Path to the Python or shell script file.
  - `allowed_capabilities` (`Collection[str] | None`, default: `None`): Explicit capability allowlist entries (such as `"read:~/.claude/projects"` or `"read:~/.codex/sessions"`).
  - `root` (`Path | None`, default: `None`): Optional base directory used to format relative `"file"` labels in returned findings.
- **Returns:** `list[dict[str, Any]]` — List of structured finding dictionaries (`"file"`, `"line"`, `"rule"`, `"severity"`, `"category"`, `"message"`).
- **Exceptions:**
  - `ValueError`: Raised if `path` is not an existing file (non-symlink).

---

### `vet_markdown`

```python
def vet_markdown(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]
```

Scans a Markdown prompt or agent instruction file (`SKILL.md`, `references/*.md`, `assets/prompt.md`, `AGENTS.md`, `.cursorrules`, etc.) for hidden bidirectional or zero-width Unicode characters, HTML comments outside fenced code blocks, prompt-override phrases, and remote Markdown or HTML images.

- **Parameters:**
  - `path` (`Path`): Path to the Markdown or instruction file.
  - `root` (`Path | None`, default: `None`): Optional base directory used to format relative `"file"` labels in returned findings.
- **Returns:** `list[dict[str, Any]]` — List of structured finding dictionaries.
- **Exceptions:**
  - `ValueError`: Raised if `path` is not an existing file (non-symlink).

---

### `vet_mcp_config`

```python
def vet_mcp_config(path: Path, *, root: Path | None = None) -> list[dict[str, Any]]
```

Inspects a Model Context Protocol configuration file (such as `.mcp.json`, `.cursor/mcp.json`, or `.vscode/mcp.json`) for shell-wrapped server commands, unpinned `npx -y` package execution, unencrypted or remote network endpoints, inline secret tokens, and hidden Unicode or prompt-override strings.

- **Parameters:**
  - `path` (`Path`): Path to the MCP JSON configuration file.
  - `root` (`Path | None`, default: `None`): Optional base directory used to format relative `"file"` labels in returned findings.
- **Returns:** `list[dict[str, Any]]` — List of structured finding dictionaries.
- **Exceptions:**
  - `ValueError`: Raised if `path` is not an existing file (non-symlink).

---

### `vet_skill`

```python
def vet_skill(
    package: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    trust_builtin_allowlists: bool = True,
    root: Path = ROOT,
) -> dict[str, Any]
```

Vets a standalone skill package directory across `SKILL.md`, `references/*.md`, `assets/prompt.md`, `.svg` assets, `agents/*.yaml`, `.mcp.json`, `.cursor-plugin/plugin.json`, `hooks/*`, bundled scripts (`scripts/*`, `.js`/`.ts`, `#!`), and filesystem containment boundaries without requiring repository authoring schemas.

- **Parameters:**
  - `package` (`Path`): Skill package directory path.
  - `allowed_capabilities` (`Collection[str] | None`, default: `None`): Additional capability allowlist entries merged into the effective allowlist.
  - `trust_builtin_allowlists` (`bool`, default: `True`): Whether to merge `SKILL_CAPABILITY_ALLOWLIST` for trusted toolkit skills (set to `False` when scanning untracked third-party skills).
  - `root` (`Path`, default: repository root): Toolkit root directory.
- **Returns:** `dict[str, Any]` — Vetting summary dictionary containing `"target"`, `"kind"`, `"skill"`, `"passed"`, `"scanned_files"`, and `"findings"`.
- **Exceptions:**
  - `ValueError`: Raised if `package` is not a directory.

---

### `vet_target`

```python
def vet_target(
    target: Path,
    allowed_capabilities: Collection[str] | None = None,
    *,
    root: Path = ROOT,
) -> dict[str, Any]
```

Vets a standalone skill directory, an individual script/instruction/SVG/YAML/manifest/MCP file, or all agent configurations, `@<path>` Markdown shims (`CLAUDE.md`, `GEMINI.md`), host plugin/settings manifests (`.claude/settings.json`, `.cursor-plugin/plugin.json`, `gemini-extension.json`), and host skill folders in a target repository.

- **Parameters:**
  - `target` (`Path`): Skill folder, configuration file, or repository root directory to inspect.
  - `allowed_capabilities` (`Collection[str] | None`, default: `None`): Explicit capability allowlist entries applied to scanned scripts.
  - `root` (`Path`, default: repository root): Toolkit root directory used to load `catalog/hosts.json` and identify trusted toolkit skill directories.
- **Returns:** `dict[str, Any]` — Structured vetting report dictionary containing `"target"`, `"kind"`, `"passed"`, `"scanned_files"`, `"skills_scanned"`, `"configs_scanned"`, and `"findings"`.
- **Exceptions:**
  - `FileNotFoundError`: Raised if `target` does not exist.
