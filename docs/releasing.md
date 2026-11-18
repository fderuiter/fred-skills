# Release process

`VERSION` is the toolkit version; artifact schema versions are separate. Existing audit
schema 1.0 and original skill names remain compatible in 0.2.0. New run and verification
records have their own versioned contracts. Breaking a persisted schema requires migration
instructions and tests rather than quietly reinterpreting old evidence.

Before bumping `VERSION`, review the selected diff, run all checks in
[CONTRIBUTING.md](../CONTRIBUTING.md), inspect the generated explorer, execute the
synthetic walkthrough, build and verify the intended pack, review the recorded behavioral
limits, and ensure [LICENSE](../LICENSE) remains present and nonempty.

Pushes to `main` run a read-only `preflight` gate that validates `v<VERSION>`, resolves
an immutable `target_sha`, verifies `LICENSE` and `VERSION` at `target_sha`, and checks
live GitHub Releases. When `v<VERSION>` is unpublished (or exists only as a draft), the
release workflow checks out `target_sha`, runs `make check`, packages and verifies all
release archives and SHA-256 manifests, attests SLSA build provenance, pushes tag
`v<VERSION>` if missing on `origin`, and publishes (or promotes) the GitHub Release using
`docs/release-notes-<VERSION>.md` when present (or generated notes otherwise). Subsequent
`main` pushes with an already published `VERSION` skip packaging and publication cleanly.

Matching `v*` tag pushes and manual `workflow_dispatch` runs remain supported. Running
`workflow_dispatch` with `inputs.publish` (`false` by default, plus optional `inputs.tag`)
builds and uploads `release-artifacts` with read-only permissions while skipping the
privileged `release` job entirely. Setting `inputs.publish` to `true` (with optional
`inputs.tag`, defaulting to `v<VERSION>`) runs full preflight verification — requiring
`refs/tags/<tag>` on `origin` for non-`main` refs or historical tags — and performs an
idempotent recovery publication or draft promotion at `target_sha`. Ordinary pull requests
have no `contents: write` job and never invoke live model adapters.

ZIP and TAR archives contain standalone skill directories; the repository source is
still needed for root CLI tooling. Archive content uses fixed paths/order/timestamps and
permissions. ZIP compression bytes may differ across Python/zlib versions, so compare
checksums produced in the same release environment. `manifest.json` and `SHA256SUMS`
record integrity; they are not a signature or publisher attestation. Release manifests
honestly report license state and never certify host behavior.

Do not commit generated archives, personal installation receipts, real audits, or local
benchmark outputs. Keep release notes concrete: task outcome, compatibility, validation
actually executed, known gaps, and rollback/update guidance. Pull requests create no tag
or public release; publication runs only after changes merge to `main`.
