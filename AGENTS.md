# Working in fred-skills

Portable Agent Skills toolkit (audit-only default; require explicit user authorization before code fixes or remote issues; see [CONTRIBUTING.md](CONTRIBUTING.md)).

- `skills/<name>/SKILL.md`: standalone skill packages; keep references skill-local and host UI metadata in `agents/`.
- `shared/<family>/`: canonical protocols, schemas, templates, and `manifest.json` targets synced into `skills/`.
- `catalog/`: authoring inputs for 6 suites, 13 packs, 9 hosts, and 57 skills (`suites.json`, `recipes.json`, `packs.json`, `profiles.json`, `hosts.json`, `experience.json`).
- `scripts/fredlib/`: local CLI and maintenance library; distributable skills never import it.
- `evals/`: offline evaluation harness and synthetic fixtures; never treat fixture-adapter scores as model quality.

Never manually edit generated outputs: `catalog/skills.json`, `docs/catalog.md`, `.claude-plugin/marketplace.json`, `.cursor-plugin/marketplace.json`, `skills/*/.cursor-plugin/plugin.json`, `gemini-extension.json` (`scripts/build_catalog.py`); `docs/experience.md`, `docs/explorer.html` (`scripts/build_experience.py`); or `skills/*/` files synced from `shared/*/manifest.json` (`scripts/sync_audit_resources.py`, including `skills/repo-audit-synthesis/scripts/audit_to_sarif.py`).

Trapdoors:
- `PYTHONDONTWRITEBYTECODE=1`: `scripts/validate_skills.py` rejects `__pycache__` and `.pyc` files under `skills/`.
- `unittest` only: run `python -m unittest discover -s tests -v`, never `pytest` (`evals/fixtures/oracles/{a,b}/test_fee.py` collides).
- Cross-platform `mypy`: `tests/test_packaging.py` runs `mypy --platform win32 --no-incremental` (guard POSIX calls with `sys.platform != "win32"`).
- Action pins: `.github/workflows/*.yml` 40-char SHAs and `# v...` tags must match `.github/action-pins.json`.
- Pages docs: tests check structural sections/tables in `README.md`, `docs/experience.md`, `docs/implementation-status.md`, `docs/releasing.md`, and `docs/runbooks.md` (and ban hosting provider names in Pages docs).

Run checks or auto-fixes from the root (see [CONTRIBUTING.md](CONTRIBUTING.md) for phase and script commands):

```sh
make check
make fix
python scripts/run_checks.py
```
