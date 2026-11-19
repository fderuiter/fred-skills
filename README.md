# fred-skills

Portable engineering workflows for investigating repositories, planning useful fixes,
and verifying that those fixes work. **55 standalone skills** across 6 suites: 34 audit
domains, a coordinator, diagnostics (`repo-audit-diagnostics`), findings synthesis, six
workflows from finding verification to remediation reconciliation, and twelve architecture,
code review, automated testing, and spec-driven development skills (experimental maturity).

Start with a task in the [task guide](docs/experience.md), browse the
[hosted documentation and explorer](https://fderuiter.github.io/fred-skills) on the web (or open the local
[offline explorer](docs/explorer.html) after cloning), or choose a package from the [catalog](docs/catalog.md).

| I want to… | Start here |
| --- | --- |
| Review a pull request | `review-pr` with an explicit base revision |
| Investigate a bug | `investigate-bug` and a bounded critical workflow |
| Understand hidden contracts | `understand-repo` or `repo-audit-program` |
| Specify a feature or design system architecture | `specify-feature` or `design-architecture` |
| Run a differential code review | `review-code-changes` with an explicit base revision |
| Expand automated test coverage | `expand-automated-tests` (add `--allow-fixes` to write test files) |
| Turn findings into a practical plan | `plan-findings` |
| Correct and independently verify a defect | `apply-fix`, then `verify-fix` |
| Prepare a release | `release-ready` |

## Installation

Register the marketplace catalog, then install the `fred-skills` suite plugin, a curated pack (such as `complete` for all 55 skills or `starter` for 6 core audit skills), or any individual skill:

### Claude Code

```sh
/plugin marketplace add fderuiter/fred-skills
/plugin install fred-skills@fred-skills
# Or install a curated pack:
/plugin install starter@fred-skills
/plugin install complete@fred-skills
```

### Skills CLI

Install skills across supported agent hosts with the open [`skills`](https://skills.sh/docs) CLI:

```sh
npx skills add fderuiter/fred-skills
# Or install a single specialist:
npx skills add fderuiter/fred-skills --skill repo-audit-concurrency
```

### Codex

Install skills into `.agents/skills/` using OpenAI Codex's built-in `$skill-installer` or the local CLI:

```text
$skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-concurrency
```

```sh
python scripts/fred.py install --destination /path/to/project --host codex --pack starter
```

Replace `fred-skills`, `complete`, or `starter` with any of the 12 packs in [`catalog/packs.json`](catalog/packs.json) or an individual skill name (for example, `repo-audit-concurrency@fred-skills`). Running `python scripts/fred.py install` without flags installs the `starter` pack across detected workspace hosts (or bootstraps `.agents/skills/` when no host markers exist).

## Try the local walkthrough

Use Python 3.12+; the synthetic walkthrough also needs Node.js 20+.

```sh
python -m venv .venv
# Activate: source .venv/bin/activate (macOS/Linux)
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python examples/walkthrough/run_demo.py --output audit-output/demo
```

Open `audit-output/demo/report.html`. The example executes an intentionally unsafe
original and a separate corrected variant, then creates a report, local issue draft,
and verification record. It makes no remote changes and invokes no AI model.

## Use it on your repository

Run these commands from this toolkit's root, replacing the repository path:

```sh
python scripts/fred.py search "stale response"
python scripts/fred.py preflight /path/to/project
python scripts/fred.py plan /path/to/project --task review-pr --mode changes --base main
python scripts/fred.py install --destination /path/to/project --host codex --pack starter --dry-run
python scripts/fred.py install --destination /path/to/project --host codex --pack starter
python scripts/fred.py init /path/to/project --task review-pr --mode changes --base main --output audit-output/review
```

Give `audit-output/review/prompt.md` to your agent. Planning selects work and records
source identity; **it does not execute an audit**. Budgets show deferred topics rather
than implying complete coverage. Use `--allow-fixes` only within a user-authorized
correction task. Remote issue publication is a separate explicit choice.

The managed installer supports all nine cataloged agent hosts (Codex, Claude Code,
Antigravity, Gemini CLI, Cursor, GitHub Copilot, OpenCode, Windsurf, and Kiro),
with dry runs, local-change protection, receipts, backups, and rollback.
Copy-path checks do not establish behavior on those hosts. Manual directory copying
remains supported; Python is unnecessary for instruction-only use.

## Release archives and supply chain verification

Official releases provide standalone tarball archives for each of the 55 skills
(e.g., `repo-audit-concurrency.tar.gz`), as well as a consolidated suite archive
(`fred-skills-suite.tar.gz`). Every published archive is accompanied by a SHA-256
checksum manifest (`checksums.sha256`) and cryptographically signed SLSA provenance
attestations.

### Download release artifacts

Download individual specialist packages or the full suite from GitHub Releases:

```sh
# Single skill archive and manifest
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/repo-audit-concurrency.tar.gz
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/checksums.sha256

# Complete audit suite archive
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-suite.tar.gz
```

### Verify integrity and build provenance

Verify cryptographic checksums and build provenance before extracting:

```sh
# Verify SHA-256 checksum for downloaded archives
sha256sum --ignore-missing -c checksums.sha256

# Verify SLSA build provenance attestation using GitHub CLI
gh attestation verify repo-audit-concurrency.tar.gz --repo fderuiter/fred-skills
gh attestation verify fred-skills-suite.tar.gz --repo fderuiter/fred-skills
```

### Install into an agent environment

Extract the verified tarball directly into your agent's skills directory:

```sh
mkdir -p /path/to/project/.agents/skills/

# Extract single specialist (creates repo-audit-concurrency/ directory)
tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/

# Extract complete suite (creates all 55 skill directories)
tar -xzf fred-skills-suite.tar.gz -C /path/to/project/.agents/skills/
```

See the [CLI guide](docs/toolkit.md) for resuming work, source freshness, reports,
comparisons, scanner imports, project profiles, and installation recovery. See
[operational runbooks](docs/runbooks.md) for failure recovery, lock contention,
corrupted receipts, and Git resolution errors. See [usage](docs/usage.md) for
portable invocation and [evaluations](evals/README.md) for baseline/with-skill
adapters, raw fixtures, and grading limits.

## Trust and validation

Findings retain intended behavior, source evidence, safe controls, uncertainty, and
actual validation. Remediation status is recorded separately from evidence certainty.
A finding absent from a later run is not automatically fixed. Local tools have no
telemetry or built-in network calls; your selected agent/adapter has its own behavior.

The [validation record](docs/validation.md) distinguishes deterministic tests, sampled
agent runs, and unexecuted scenario specifications. Host behavior and broad model
quality are not certified. The [implementation status](docs/implementation-status.md)
records delivered features and remaining validation or owner decisions.

Run automated validation locally with:

```sh
make check
```

Use `make fix` to repair drift or regenerate shared copies and discovery catalogs. For environments without `make`, direct Python commands remain supported as documented in [CONTRIBUTING.md](CONTRIBUTING.md).

For maintenance, see [CONTRIBUTING.md](CONTRIBUTING.md),
[architecture](docs/architecture.md), [runbooks](docs/runbooks.md), [security](SECURITY.md), and
[research](docs/research.md). Distributed under the MIT [LICENSE](LICENSE) (see
[licensing](docs/licensing.md)); pushes to `main` automatically tag and publish verified
`VERSION` releases as documented in [releasing](docs/releasing.md).
