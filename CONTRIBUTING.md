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

Once installed, fast static, `mypy` type, and Markdown formatting hooks run on every `git commit`, while `unit-tests` runs on `git push` (`pre-push`). You can also execute either stage manually across the repository:

```sh
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

To bypass hooks for emergency fixes, use `--no-verify` (e.g. `git commit --no-verify`) or skip specific checks with `SKIP=<hook-id> git commit`.

Run verification checks with `make` (`make check` delegates to `scripts/run_checks.py` to run Ruff linting, code formatting checks, mypy static type checks, package validation, and unit tests):

```sh
make check
```

To automatically format code and apply safe lint fixes with Ruff, repair drift, or regenerate shared copies and discovery indexes:

```sh
make fix
```

To run the unit test suite:

```sh
make test
```

`make check` and `make fix` run Ruff automatically (`make check` also runs `mypy`). For environments without `make` (including Windows), run the unified cross-platform runner or individual check commands:

```sh
# Full cross-platform check suite and targeted phases
python scripts/run_checks.py
python scripts/run_checks.py --phase lint
python scripts/run_checks.py --phase format
python scripts/run_checks.py --phase typecheck
python scripts/run_checks.py --phase packages
python scripts/run_checks.py --phase docs
python scripts/run_checks.py --phase tests

# Individual linting, formatting, static type, and verification commands
ruff check .
ruff format --check .
python -m mypy
python scripts/sync_audit_resources.py --check
python scripts/build_catalog.py --check
python scripts/build_experience.py --check
python scripts/validate_skills.py
python scripts/check_doc_links.py
python scripts/verify_doc_snippets.py
python scripts/check_workflow_pins.py --check
python scripts/verify_workflow_actions.py --check
python -m unittest discover -s tests -v

# Regenerate shared copies and indexes
python scripts/sync_audit_resources.py
python scripts/build_catalog.py
python scripts/build_experience.py

# Scaffold a new skill package and export diff-scoped validation telemetry
python scripts/scaffold_skill.py <skill-name> --dry-run
python scripts/scaffold_skill.py <skill-name> --catalog --suite repository-workflows --dry-run
python scripts/validate_skills.py --base-ref main --telemetry validation-telemetry.json
```

Manual Ruff commands (`ruff format .` and `ruff check --fix .`) are needed only when `make` is absent, as `make fix` invokes them automatically.

All commands above run without network access once the development dependencies are installed; CI additionally runs `python scripts/check_workflow_pins.py --verify-remote` to confirm upstream commit and tag resolution via `git ls-remote`.
The ledger validator also needs jsonschema; instruction-only use does not need Python.

## Change a skill

1. Scaffold a compliant starter package with `python scripts/scaffold_skill.py <skill-name>`
   (add `--catalog --suite <suite>` to update `catalog/suites.json`, `catalog/experience.json`,
   and generated discovery indexes), then establish a real task, its expected output, and
   when the skill should activate.
2. Put a brief, specific trigger in `description` (`1–1024` characters, unique across skills)
   with `name` matching the directory; put procedure in the `SKILL.md` body under `500` lines.
   Link package-local references directly without symlinks or paths escaping the skill directory.
   Preserve the distinction between independent requirements and observed code.
3. Enforce all seven package validator guardrails (`scripts/validate_skills.py`):
   - **Prompt budgets**: `assets/prompt.md` is required, must stay within `1200` characters
     (`MAX_PROMPT_CHARS = 1200`) and `20` lines (`MAX_PROMPT_LINES = 20`), and must not contain
     Markdown headings or triple-backtick code fences.
   - **Allowed placeholders**: Any `<...>` token in `assets/prompt.md` must match the approved
     allowlist: `<audit workspace/reports>`, `<critical workflows>`,
     `<existing audit workspace, if any>`, `<existing audit workspace>`,
     `<finding/run artifacts>`, `<path-to-diagnostic-bundle-output>`,
     `<path-to-run-workspace-directory>`, `<path-to-session-transcript>`,
     `<repository/revision>`, `<repository>`, `<revision>`.
   - **Negative constraints**: `assets/prompt.md` must include explicit negative prohibitions
     (such as `do not`, `never`, `must not`, `cannot`, `should not`, `prohibit`, `forbid`,
     `avoid`, or `disallow`) and reference the skill's output schema contract.
   - **Boundary headings**: `SKILL.md` must include a dedicated `## Guardrails` or `## Deliver`
     boundary section containing explicit negative prohibitions, and link every
     `assets/*.schema.json` output schema directly within 1-hop reference depth.
   - **Output schemas**: Define at least one closed Draft 2020-12 JSON output schema in
     `assets/*.schema.json` (excluding `evaluation-scenarios*`) with root `properties` and
     `additionalProperties: false` on structured objects.
   - **UI limits**: `agents/openai.yaml` must provide nonempty `interface.display_name`, unique
     `interface.short_description` of `25–64` characters without repeated adjacent words,
     `interface.default_prompt` referencing `$<skill-name>`, package-local SVG `icon_small` /
     `icon_large` (`assets/icon.svg`), and boolean `policy.allow_implicit_invocation`.
   - **Scenario suffixes**: `references/evaluation-scenarios.json` must conform to
     `schemas/evaluation-scenarios.schema.json` with three scenarios using `-failure`, `-safe`,
     and `-routing` identifier suffixes (including `sibling_skill` on `-routing` scenarios for
     near-neighbor activation routing). Keep expected answers out of fresh-context execution prompts.
4. Run packaging checks and relevant deterministic tests. Initial outcomes for newly authored
   skills are recorded in `.fred-skills/validation-attempts.json` so local revision loops preserve
   first-pass telemetry. For behavioral changes, run representative agent tasks and record actual
   results, misses, and limits in the PR.
5. Regenerate shared copies and indexes. Review the generated diff for unrelated changes.

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

Before bumping `VERSION` for a release, read [docs/releasing.md](docs/releasing.md) and
[docs/licensing.md](docs/licensing.md): merging an unpublished `VERSION` to `main`
automatically tags `v<VERSION>` and publishes verified release archives under the MIT
[LICENSE](LICENSE). Real host certification and broad model quality benchmarks should not
be inferred from this repository's package structure or fixture results.
