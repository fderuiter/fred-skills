# Repository architecture

## Distribution boundary

Every `skills/<name>/` directory is a complete Agent Skills package. Its frontmatter
name matches the directory, and relative resource references resolve within it.
There are no symlinks or required imports from sibling packages. A user can export
one specialist without copying the whole repository.

This follows the [Agent Skills specification](https://agentskills.io/specification).
Concise entry instructions and conditionally loaded references follow the
[authoring guidance](https://agentskills.io/skill-creation/best-practices).

## Canonical shared resources and committed copies

The audit family uses one protocol/schema/report/handoff contract. Canonical resources
live under `shared/repo-audit/resources/`; `shared/repo-audit/manifest.json` lists them.
`scripts/sync_audit_resources.py` writes identical copies into the family members
defined in `catalog/suites.json`. Its `--check` mode only reports drift and exits nonzero.

Committing these generated copies is intentional: it preserves independent installation
while keeping one authoring source of truth. Symlinks and upward-relative links would
make a standalone export depend on repository layout. CI checks byte equality so
separately edited protocol/schema copies cannot silently diverge.

## Discovery and execution order

`catalog/suites.json` defines membership and ordered passes. `scripts/build_catalog.py`
reads actual skill metadata and emits `catalog/skills.json` and `docs/catalog.md`.
These outputs contain stable relative paths and no generation timestamps or personal
installation IDs. The generator rejects missing or multiply assigned suite members.

Flat `skills/<name>/SKILL.md` placement supports normal skill discovery. Root authoring
scripts, docs, fixtures, and CI stay outside the installable packages. Optional
`agents/openai.yaml` files are a separate host adapter; portable instructions do not
assume a particular agent API or subagent capability.

## Evidence boundary

The ledger and report formats are shared interfaces, not a new agent framework. Audits
retain independently supported intended behavior, observed implementation, safeguards,
failure traces, uncertainty, and honest coverage. The coordinator reuses IDs between
passes and synthesis groups findings by corrective mechanism and authority.

The audit program is sequential by default. Authorized delegation can use independent
workers and one merger, without concurrent edits to the canonical ledger. Application
code changes and remote issue publication remain controlled by the user's task.

## Validation boundary

Repository checks cover metadata, directory names, schemas, relative links, optional
UI metadata, shared-copy drift, generated indexes, and selected ledger failure modes.
Tests use synthetic records and malformed packages, including known unsafe fixtures;
none constitute proof that all 33 specialists detect every defect on every host.

CI runs on ordinary pull requests and main-branch pushes with read-only repository
permissions, pinned action revisions, and checkout credential persistence disabled.
It validates the repository without invoking an external agent or requiring secrets.

## Optional local toolkit

The original 35-package audit family remains intact. A six-package workflow family
uses `shared/repo-workflows/` for finding verification, independent regression specification,
fix planning/application/verification, and reconciliation. The resource synchronizer reads
family manifests; each resulting package is independently portable.

Root `scripts/fred.py` provides discovery and explicitly selected local operations.
`fredlib` separates source inventory/Git metadata, planning/run state, installation,
rendering, evaluation, and release archives. It neither dispatches an agent nor installs
an analyzer. Standard library code handles filesystem/subprocess work; jsonschema and
PyYAML validate records and opt-in configuration. Playwright is optional browser QA only.

Task recipes/packs/profiles/host locations are data, separate from skill instructions.
Generated discovery combines actual package metadata with authored experience fields;
CI rejects drift. Run manifests are additive, leaving audit schema 1.0 unchanged.
Evidence certainty and remediation progress are separate axes. Resume preserves the old
snapshot and requests impact review; comparisons never close missing findings.

Only explicit adapters execute processes during evaluation. Raw source and optional skill
are copied into fresh work directories; grading happens afterward, with expected keys
outside the prompt. This is a contamination control, not a security sandbox. Reports
escape untrusted text, include no external resources, and cannot execute issue publication.
