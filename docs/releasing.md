# Release process

`VERSION` is the toolkit version; artifact schema versions are separate. Existing audit
schema 1.0 and original skill names remain compatible in 0.2.0. New run and verification
records have their own versioned contracts. Breaking a persisted schema requires migration
instructions and tests rather than quietly reinterpreting old evidence.

Before publication, review the selected diff, run all checks in CONTRIBUTING.md, inspect
the generated explorer, execute the synthetic walkthrough, build and verify the intended
pack, and review the recorded behavioral limits. Choose a public LICENSE as owner.

The release workflow can be run manually to build a pack as a workflow artifact. Its
optional publication input creates a **draft** GitHub release only for an existing
`v<VERSION>` tag and a nonempty LICENSE. A `v*` tag also prepares the draft when its
version matches. Publishing that draft remains a separate maintainer action. Ordinary
pull requests have no contents-write job and never invoke live model adapters.

ZIP and TAR archives contain standalone skill directories; the repository source is
still needed for root CLI tooling. Archive content uses fixed paths/order/timestamps and
permissions. ZIP compression bytes may differ across Python/zlib versions, so compare
checksums produced in the same release environment. `manifest.json` and `SHA256SUMS`
record integrity; they are not a signature or publisher attestation. Release manifests
honestly report license state and never certify host behavior.

Do not commit generated archives, personal installation receipts, real audits, or local
benchmark outputs. Keep release notes concrete: task outcome, compatibility, validation
actually executed, known gaps, and rollback/update guidance. This PR creates no tag or
public release.
