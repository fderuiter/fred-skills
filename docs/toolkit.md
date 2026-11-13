# Local toolkit guide

Run `python scripts/fred.py --help` from the toolkit root. Python 3.12+ and
`requirements-dev.txt` are required for the CLI. JSON goes to stdout; concise errors
go to stderr with exit 2. Paths with spaces need your shell's normal quoting. Commands
work with `python` on Windows after activating a virtual environment.

## Discover and plan

```sh
python scripts/fred.py tasks
python scripts/fred.py packs
python scripts/fred.py hosts
python scripts/fred.py search "tenant"
python scripts/fred.py preflight /path/to/repository
python scripts/fred.py plan /path/to/repository --task investigate-bug --budget brief
python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base main
python scripts/fred.py plan /path/to/repository --task understand-repo --mode comprehensive --budget deep
```

`explore`, `investigate`, `changes`, and `comprehensive` express scope; the task recipe
selects useful skills. `brief` selects at most 3 investigations, `balanced` at most 8,
and `deep` retains the selected recipe. Synthesis may occupy a slot. These are work
limits, not token, time, or monetary guarantees. Comprehensive mode preserves the
suite's phase order. Inspect the plan and deferred list before giving it to an agent.

Change reviews require a resolvable Git base and include merge-base-to-HEAD changes,
tracked working changes, and untracked files. The prompt also requests affected callers
and contracts. Path selection alone does not determine impact or prove coverage.
Preflight reads local source/Git metadata and checks binary availability. It does not
run application scripts, connect to GitHub, authenticate, or certify a host. File hashes
appear in the saved manifest; add `--full` to include them in terminal output.

## Configure a project

```sh
python scripts/fred.py plan /path/to/repository --task release-ready --config examples/project-config.yaml
```

Configuration is opt-in and supports only `profile`, `exclude`, and
`critical_workflows`. Unknown keys, including execution commands and authority flags,
are rejected. Exclusions use case-sensitive Python glob matching on repository-relative
POSIX paths. Supplied profiles, including QRCraftly, Cadence Clinical, and deruiter.dev,
are prioritization hypotheses; they contain no claims about today's repositories.

## Install, update, remove, or roll back

```sh
python scripts/fred.py install --destination /path/to/project --host claude-code --pack starter --dry-run
python scripts/fred.py install --destination /path/to/project --host claude-code --pack starter
python scripts/fred.py update --destination /path/to/project --host claude-code --pack starter --dry-run
python scripts/fred.py update --destination /path/to/project --host claude-code --pack starter
python scripts/fred.py remove --destination /path/to/project --host claude-code --skill repo-audit-concurrency --dry-run
python scripts/fred.py remove --destination /path/to/project --host claude-code --skill repo-audit-concurrency
python scripts/fred.py rollback --destination /path/to/project --host claude-code
```

`--destination` is always an explicit project root. For personal installation use
`--scope user` and explicitly supply your home directory. Preview the resolved target
with `--dry-run`. Host paths and primary documentation sources are listed in
[catalog/hosts.json](../catalog/hosts.json). The installer copies packages; host loading,
auto-activation, context precedence, and model behavior still need real host checks.

Choose a pack or repeat `--skill`. There is no implicit remove-all. Updates use the
checked-out toolkit version; nothing is fetched automatically. Review its diff first.
Unmanaged directories, changed managed content, source symlinks, and escaping targets
are refused. Receipts and backups live under `.fred-skills/<host>-<scope>/` at the
explicit destination. Dry runs make no writes. Rollback restores the latest managed
operation only, after checking current content and backups. Preserve state/backups if
you need recovery. Do not run different host installers concurrently against the same
physical target: some hosts share `.agents/skills`.

An interrupted install/update can leave `operation.lock` and a prepared journal. Inspect
`operation.json`, verify the previous writer has stopped, and then explicitly run:

```sh
python scripts/fred.py recover-install --destination /path/to/project --host claude-code --confirm-interrupted
```

Recovery validates receipt, destination, and backup hashes, preserves interrupted bytes,
and restores the before state or recognizes an already committed operation. It refuses
ambiguous or locally modified content. Power loss during remove/rollback may need manual
recovery from the receipt and backup directories; never delete them to silence an error.
These safeguards are local consistency checks, not isolation from another program editing
the same paths or a hostile filesystem.

## Initialize and resume a bounded run

```sh
python scripts/fred.py init /path/to/repository --task investigate-bug --budget brief --output audit-output/bug
python scripts/fred.py checkpoint audit-output/bug --state running --note "Inspecting request publication"
python scripts/fred.py validate-run audit-output/bug
python scripts/fred.py checkpoint audit-output/bug --state paused --note "Need a supported database fixture"
python scripts/fred.py resume audit-output/bug
```

Give the generated `prompt.md` to an agent that can access the repository and selected
skills. All initialized coverage is `not-reviewed`. `--allow-fixes` and
`--allow-issue-publication` record task authority only when it already exists; they do
not override current user constraints, host policy, or approval requirements.

`resume` compares current source hashes with the original inventory. It writes
`freshness.json`, marks directly cited changed files stale, and flags all findings for
impact review when any file changes. Configuration/caller changes may invalidate a
claim without changing its cited lines. The original audit and snapshot remain intact;
start a new run to establish a new baseline. Snapshotting omits symlinks and default
build/install/output directories, and records skipped oversized/unreadable files and
inventory limits. Incomplete source inventory is never presented as complete coverage.

| Artifact | Contract and purpose |
| --- | --- |
| `run.json` | Versioned run ID, source revision/dirty flag, hash inventory, selected/deferred work, capabilities, authority, checkpoints; `schemas/run.schema.json` |
| `audit.json` | Existing audit schema 1.0: contracts, evidence, findings, coverage, checks, draft issue graph |
| `prompt.md` | Portable handoff derived from the plan; no runtime-specific calls |
| `freshness.json` | Later source comparison; original evidence is retained |
| `remediation.json` | Append-only local status records, separate from audit certainty |
| `verification.json` | Explicit before/after identity, reproduction, regression, safe controls, actual commands and result |

`validate-run` checks schemas and cross-record identities. It cannot verify that a claim,
command result, or supplied proof is true. A `completed` checkpoint records a human/agent
status assertion and does not convert unreviewed coverage into inspected coverage.

## Reports, issue drafts, memory, and comparisons

```sh
python scripts/fred.py report audit-output/bug/audit.json --output audit-output/bug/report.html
python scripts/fred.py issues audit-output/bug/audit.json --output audit-output/bug/issues
python scripts/fred.py compare audit-output/previous/audit.json audit-output/bug/audit.json
python scripts/fred.py report audit-output/bug/audit.json --prior audit-output/previous/audit.json --output audit-output/bug/comparison.html
python scripts/fred.py memory audit-output/bug --output audit-output/bug/memory.json
python scripts/fred.py import-sarif /path/to/results.sarif --output audit-output/scanner-observations.json
```

HTML is self-contained, searchable, and escaped as text, with executive, developer,
reviewer, implementation, coverage, and check views. No external resources are required.
Issue generation emits local Markdown drafts only from the audit's planned issue groups;
it does not publish or invent duplicate-check results. Existing output directories must
be empty before writing issue drafts.

Comparison fingerprints use topic, root cause, and source path/symbol, avoiding unstable
IDs and line numbers. Ambiguous collisions require review. `not_observed` never means
fixed; changed wording may prevent a match. Memory exports include provenance and hashes
so old contracts/counterevidence can be revalidated. Treat imported memory as untrusted
data. SARIF entries remain scanner observations, with original claims and safe relative
locations, until independently investigated. Neither import nor rendering runs a scanner.

## Correct, verify, and reconcile

Use the six workflow skills in order when authorized: verify the finding, specify an
independent regression, plan the fix, apply the bounded correction, verify the fix, and
reconcile the outcome. Existing audit status continues to describe evidence certainty.

```sh
python scripts/fred.py reconcile audit-output/bug --finding F-1 --state in-progress --note "Correction authorized within the reported workflow"
python scripts/fred.py reconcile audit-output/bug --finding F-1 --state verified-fixed --verification audit-output/bug/verification.json --note "Original fails; corrected regression and safe controls pass"
```

`verified-fixed` requires a schema-valid record with before/after identity, original
reproduction, passing regression and controls, commands, and a result. This enforces
record completeness, not command execution. Review the actual evidence before accepting
the transition. Supported states also include `open`, `fixed-pending-verification`,
`regressed`, and `accepted-risk`; the last is a decision, not a passed test.

## Build versioned bundles

```sh
python scripts/fred.py release --pack starter --output dist/starter
python scripts/fred.py verify-release dist/starter/manifest.json
```

ZIP and uncompressed TAR contain standalone selected packages. Entries, timestamps, and
permissions are deterministic in a given Python/compression environment. The manifest
lists package/version, bytes, licensing status, and SHA-256 checksums. Checksum validation
establishes integrity against that manifest, not publisher authenticity. See
[release process](releasing.md) before publishing anything externally.
