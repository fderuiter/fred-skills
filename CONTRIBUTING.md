# Contributing

## Layout and ownership

`skills/` contains installable packages, one named directory per skill. A package
includes `SKILL.md` and only the scripts, references, templates, and optional host
metadata needed for its task. Root documentation and authoring tests stay outside it.

`shared/repo-audit/resources/` owns the common protocol, audit schema, report template,
and handoff template. Its manifest names every generated destination. The copies inside
each audit skill are committed to make a single exported skill self-contained, without
symlinks, a network dependency, or paths escaping its directory. CI rejects drift.

`catalog/suites.json` owns family membership and pass ordering. `catalog/skills.json`
and `docs/catalog.md` are deterministic generated discovery indexes. New families can
be added without changing the audit suite's public names.

## Local checks

Use Python 3.12 or newer in a virtual environment:

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r requirements-dev.txt
pre-commit install
```

Once installed, pre-commit hooks run automatically on every `git commit`. You can also execute all verification checks manually across the repository:

```sh
pre-commit run --all-files
```

To bypass hooks for emergency fixes, use `--no-verify` (e.g. `git commit --no-verify`) or skip specific checks with `SKIP=<hook-id> git commit`.

Run verification checks with `make`:

```sh
make check
```

To repair drift or regenerate shared copies and discovery indexes:

```sh
make fix
```

To run the unit test suite:

```sh
make test
```

For environments without `make`, run direct Python commands:

```sh
# Verification checks (CI-equivalent non-mutating checks)
python scripts/sync_audit_resources.py --check
python scripts/build_catalog.py --check
python scripts/build_experience.py --check
python scripts/validate_skills.py
python -m unittest discover -s tests -v

# Regenerate shared copies and indexes
python scripts/sync_audit_resources.py
python scripts/build_catalog.py
python scripts/build_experience.py
```

All commands run without network access once the development dependencies are installed.
The ledger validator also needs jsonschema; instruction-only use does not need Python.

## Change a skill

1. Establish a real task, its expected output, and when the skill should activate.
2. Put a brief, specific trigger in `description`; put procedure in the Markdown body.
3. Keep root instructions below 500 lines. Link references directly and say when to read
   them. Preserve the distinction between independent requirements and observed code.
4. Include a failure scenario, a safe control, and an unrelated activation scenario.
   Keep expected answers out of fresh-context execution prompts.
5. Run packaging checks and relevant deterministic tests. For behavioral changes, run
   representative agent tasks and record actual results, misses, and limits in the PR.
6. Regenerate shared copies and indexes. Review the generated diff for unrelated changes.

When changing the shared protocol/schema, review every consumer and update the version
and migration guidance together if the artifact contract changes. A schema passing does
not mean its claims are true. Prefer small patches and an observable acceptance condition
over a blanket instruction to follow best practices.

## Pull requests

Describe the user-visible workflow change, why it is needed, and relevant validation.
Distinguish executed tests from evaluation specifications. Identify real compatibility
limits; do not claim support for an agent host solely because it recognizes SKILL.md.
Use synthetic fixtures and exact relative paths. Do not commit actual audits of private
repositories or embed secrets, personal installation URLs, or transient conversation IDs.

Research sources are attribution and method references, not bundled third-party code.
Do not infer permission to copy an upstream implementation from its public availability.

## Toolkit and experience changes

`shared/repo-workflows/` owns common correction handoffs/protocol. Its manifest limits
copies to the new workflow family. `catalog/{recipes,packs,profiles,hosts,experience}.json`
are authoring inputs; the skill catalog, docs/catalog.md, docs/experience.md, and explorer
are generated. Keep these boundaries explicit rather than adding another editable index.

Root CLI tooling lives in `scripts/fredlib/`; installable skills do not import it.
Preserve audit schema 1.0 compatibility, source identity, explicit authority, empty findings,
local-change protection, and draft-only remote behavior. A recorded proof is not execution.
Add meaningful regressions for changes to these contracts. Keep live adapters and model
credentials outside ordinary CI. See evals/README.md for the adapter protocol.

The Ubuntu/Windows jobs run deterministic tests, the Node walkthrough, and pack verification.
For optional local browser checks, install requirements-browser.txt and run
`python -m playwright install chromium`, then `python tests/browser_check.py`.
Chromium needs ordinary process/socket support; if a managed environment prevents launch,
record that limitation and use CI. Do not weaken the environment's restrictions.

Before requesting a release, read docs/releasing.md and docs/licensing.md. No owner
license, public release, real host certification, or quality benchmark should be inferred
from this repository's package structure or fixture results.
