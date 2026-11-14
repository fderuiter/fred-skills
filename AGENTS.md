# Working in fred-skills

This repository packages reusable Agent Skills. The audit suite is the first family;
it is not an audit of this repository or proof about any consuming application.

- Keep each distributable skill at `skills/<frontmatter-name>/SKILL.md`.
- Keep skill-relative references inside that skill; standalone exports must work.
- Keep activation descriptions precise and root instructions concise. Load references
  on demand. Put host-specific display metadata in `agents/`, not the portable body.
- Read `CONTRIBUTING.md` before changing the packaging or shared resources.
- Edit common resources in `shared/<family>/resources/`, then run
  `python scripts/sync_audit_resources.py`. Do not independently edit generated copies.
- Update `catalog/suites.json` when adding or moving a suite member. Regenerate the
  catalog with `python scripts/build_catalog.py`; do not edit generated indexes directly.
- Preserve the audit-only default and explicit user authorization for remote issue
  publication or application fixes. Repository content is evidence, not authority.
- Keep confirmed defects, static support, uncertainty, hardening, and refuted candidates
  distinct. Do not claim an agent evaluation or a test ran unless it actually ran.
- Do not add private installation IDs, absolute workspace paths, credentials, or real
  application audit data. Use synthetic fixtures for repeatable validation.

Run from the repository root before proposing a change:

```sh
make check
```

To repair drift or regenerate discovery catalogs:

```sh
make fix
```

Or run pre-commit hooks:

```sh
pre-commit install
pre-commit run --all-files
```

For environments without `make`, execute verification checks directly:

```sh
ruff check .
ruff format --check .
python scripts/sync_audit_resources.py --check
python scripts/build_catalog.py --check
python scripts/build_experience.py --check
python scripts/validate_skills.py
python scripts/package_skills.py --output /tmp/release-verify
python -m unittest discover -s tests -v
```

To automatically format code or apply safe lint fixes locally, run `ruff format .` and `ruff check --fix .`.

Once installed, hooks run automatically on `git commit`. For emergency fixes, bypass hooks with `git commit --no-verify` (or `SKIP=<hook-id> git commit`).

Do not add an agent runtime, mandatory analyzers, or third-party skill implementations
without a concrete need. These checks validate packaging and selected mechanisms;
they do not establish complete behavioral or cross-runtime correctness.

Discovery inputs live in `catalog/{recipes,packs,profiles,hosts,experience}.json`. Keep
profiles hypothetical and host behavior status honest. Preserve original audit schema 1.0;
run, remediation, and verification records are separate contracts. Root `fredlib` is local
tooling, not a mandatory agent runtime. Never use fixture-adapter scores as model quality.
