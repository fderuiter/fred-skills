# Release process

`VERSION` is the toolkit version; artifact schema versions are separate. Existing audit
schema 1.0 and original skill names remain compatible in 0.2.0. New run and verification
records have their own versioned contracts. Breaking a persisted schema requires migration
instructions and tests rather than quietly reinterpreting old evidence.

Before merging distributable changes, review the selected diff, run all checks in
[CONTRIBUTING.md](../CONTRIBUTING.md), inspect the generated [offline explorer](explorer.html),
execute the synthetic walkthrough, build and verify the intended pack, review the recorded
behavioral limits, and ensure [LICENSE](../LICENSE) remains present and nonempty. Validated
pushes to `main` automatically publish the hosted explorer and documentation to
[fderuiter.github.io/fred-skills](https://fderuiter.github.io/fred-skills) via
`.github/workflows/pages.yml` after `Release` succeeds on verified `origin/main` (with
`workflow_dispatch` on `main` available to retry transient Pages outages without manual steps).

Pushes to `main` run a serialized `prepare` job (`scripts/bump_version.py`) in
`.github/workflows/release.yml` that checks changes in `skills/`, `catalog/`, `shared/`, and
`scripts/fredlib/` since `refs/tags/v<VERSION>`, computes a minor bump when `catalog/skills.json`,
`catalog/suites.json`, or `catalog/packs.json` changes membership (or a patch bump otherwise),
synchronizes `VERSION`, all 61 plugin/extension manifests, `README.md`, `docs/usage.md`, and
`docs/release-notes-<VERSION>.md` while keeping tagged `docs/release-notes-<TAG>.md` files
byte-identical, pushes the bump commit to `origin/main`, and hands the pushed `target_sha`
directly to `preflight`, `validate`, `build`, and `release` in the same run. Next, `preflight`
validates SemVer 2.0.0 `v<VERSION>`, fetches full `origin/main` history and tags to verify that
`target_sha` is an ancestor of `origin/main`, verifies nonempty `LICENSE` and matching `VERSION`
at `target_sha`, and checks live GitHub Releases. When `v<VERSION>` is unpublished (or exists
only as a draft), the release workflow calls the reusable `.github/workflows/validate.yml`
workflow (`workflow_call` with `contents: read` across Ubuntu and Windows on Python 3.12 and
3.13, `pre-commit`, the synthetic Node walkthrough, standalone pack verification, and Chromium
browser checks) before packaging deterministic release archives and SHA-256 manifests in `build`.
Next, the `release` job re-verifies `origin/main` ancestry, nonempty `LICENSE`, and remote
`refs/tags/v<VERSION>` tag binding, verifies downloaded `manifest.json`, `SHA256SUMS`, and
`checksums.sha256` completeness before attesting SLSA build provenance, serializes publication
per release tag, pushes `refs/tags/v<VERSION>` on `main` branch pushes if missing on `origin`,
and performs zero-touch GitHub Release publication (`--verify-tag` and `--target`, without
`--draft` or manual approval) using `docs/release-notes-<VERSION>.md` when present (or
`--generate-notes` otherwise, marking pre-release tags with `--prerelease`). Commits on `main`
outside distributable paths keep `VERSION` unchanged and skip duplicate publication cleanly.

Matching `v*` tag pushes and manual `workflow_dispatch` runs remain supported. Running
`workflow_dispatch` with `inputs.publish` (`false` by default, plus optional `inputs.tag`)
executes the reusable `.github/workflows/validate.yml` suite and builds/uploads verified
`release-artifacts` with read-only permissions (without requiring a `LICENSE` or remote tag)
while skipping the privileged `release` job entirely. Setting `inputs.publish` to `true`
(with optional `inputs.tag`, defaulting to `v<VERSION>`) enforces `origin/main` commit
ancestry, nonempty `LICENSE`, and an existing remote `refs/tags/v<VERSION>` tag on `origin`
for `target_sha` before running reusable validation, signing provenance, and performing
zero-touch publication or draft promotion (`--verify-tag` and `--target`). Tags or
publication dispatches targeting commits outside `origin/main` fail fast before attestation
or release creation. Ordinary pull requests have no `contents: write` job and never invoke
live model adapters.

ZIP, TAR, and `.tar.gz` archives contain standalone skill directories; the repository
source is still needed for root CLI tooling. Archive entries use fixed POSIX path order,
normalized timestamps, and deterministic permissions (`0755` for executable skill scripts
and `0644` for regular files) with level-9 DEFLATE compression. Compressed `.zip` and
`.tar.gz` bytes may differ across Python or zlib versions, so compare checksums produced in
the same release environment. `manifest.json`, `SHA256SUMS`, and `checksums.sha256` record
integrity; they are not a signature or publisher attestation. Release manifests honestly
report license state and never certify host behavior.

Do not commit generated archives, personal installation receipts, real audits, or local
benchmark outputs. Keep release notes concrete: task outcome, compatibility, validation
actually executed, known gaps, and rollback/update guidance. Pull requests create no tag
or public release; publication runs only after changes merge to `main`.
