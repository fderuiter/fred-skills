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

To regenerate shared copies and indexes or run verification scripts directly:

```sh
python scripts/sync_audit_resources.py
python scripts/build_catalog.py
python scripts/validate_skills.py
python -m unittest discover -s tests -v
```

For CI-equivalent non-mutating checks, add `--check` to the first two scripts. All
commands run without network access once the development dependencies are installed.
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
