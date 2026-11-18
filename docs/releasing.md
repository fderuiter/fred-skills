# Release process

`VERSION` is the toolkit version; artifact schema versions are separate. Existing audit
schema 1.0 and original skill names remain compatible in 0.2.0. New run and verification
records have their own versioned contracts. Breaking a persisted schema requires migration
instructions and tests rather than quietly reinterpreting old evidence.

Before bumping `VERSION`, review the selected diff, run all checks in
[CONTRIBUTING.md](../CONTRIBUTING.md), inspect the generated explorer, execute the
synthetic walkthrough, build and verify the intended pack, review the recorded behavioral
limits, and ensure [LICENSE](../LICENSE) remains present and nonempty.

Pushes to `main` automatically check live GitHub Releases for `v<VERSION>`. When
`v<VERSION>` is not yet published (or exists only as a draft), the release workflow runs
`make check`, builds and verifies all release archives and checksums, pushes tag
`v<VERSION>` if missing on `origin`, and publishes the GitHub Release directly using
`docs/release-notes-<VERSION>.md` when present (or generated notes otherwise). Subsequent
`main` pushes with an already published `VERSION` skip packaging and publication cleanly.
Matching `v*` tags and `workflow_dispatch` remain supported for idempotent recovery runs.
Ordinary pull requests have no `contents: write` job and never invoke live model adapters.

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
