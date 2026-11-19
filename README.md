# fred-skills

Portable engineering workflows for investigating repositories, planning useful fixes,
and verifying that those fixes work. **57 standalone skills** across 6 suites: 34 audit
domains, a coordinator, diagnostics (`repo-audit-diagnostics`), findings synthesis, eight
workflows from finding verification to session hygiene, and twelve architecture,
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

Register the marketplace catalog, then install `starter` for 6 core audit skills (`<= 1,500` raw characters), another curated pack, any individual skill, or `complete` for all 57 skills:

### Claude Code

```sh
/plugin marketplace add fderuiter/fred-skills
/plugin install starter@fred-skills
/plugin install repo-audit-concurrency@fred-skills
/plugin install complete@fred-skills
/plugin install fred-skills@fred-skills
```

### Skills CLI

Install skills across supported agent hosts with the open [`skills`](https://skills.sh/docs) CLI:

```sh
npx skills add fderuiter/fred-skills --skill repo-audit-concurrency
npx skills add fderuiter/fred-skills
```

### Codex

Install skills into `.agents/skills/` using the local CLI (`starter` pack by default) or OpenAI Codex's built-in `$skill-installer`:

```sh
python scripts/fred.py install --destination /path/to/project --host codex --pack starter
```

```text
$skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-concurrency
```

Replace `starter` with any of the 13 packs in [`catalog/packs.json`](catalog/packs.json) or an individual skill name (for example, `repo-audit-concurrency@fred-skills`). Running `python scripts/fred.py install` without flags installs the 6-skill `starter` pack across detected workspace hosts (or bootstraps `.agents/skills/` when no host markers exist). **Stage-1 context budget warning:** Start with `starter@fred-skills` (`6` skills, `<= 1,500` raw characters) or focused domain packs; installing `complete@fred-skills` or `fred-skills@fred-skills` loads all 57 skills at startup, which can crowd or exceed Stage-1 discovery budgets on smaller context windows in [OpenAI Codex](https://developers.openai.com/codex/skills) (`2%` of context or `8,000`-character fallback) and [Claude Code](https://code.claude.com/docs/en/skills) (`1%` of context, `skillListingBudgetFraction = 0.01`).

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

Official releases provide versioned curated pack archives led by the 6-skill `starter` pack (`fred-skills-0.2.0-starter.tar`),
standalone tarball archives for each of the 57 skills (e.g., `repo-audit-concurrency.tar.gz`), and a consolidated suite archive
(`fred-skills-suite.tar.gz`), accompanied by SHA-256 checksum manifests (`SHA256SUMS` and `checksums.sha256`) and signed SLSA provenance attestations.
**Stage-1 context budget warning (`fred-skills-suite.tar.gz`):** Extracting `fred-skills-suite.tar.gz` installs all 57 skills at once, which can crowd or exceed Stage-1 skill-listing budgets on smaller context windows in [OpenAI Codex](https://developers.openai.com/codex/skills) (`2%` of context or `8,000`-character fallback) and [Claude Code](https://code.claude.com/docs/en/skills) (`1%` of context, `skillListingBudgetFraction = 0.01`);
prefer `fred-skills-0.2.0-starter.tar` (`6` skills, `<= 1,500` raw characters) or individual skill tarballs by default.

### Download release artifacts

Download the `starter` pack archive, an individual specialist package, or the full suite from GitHub Releases:

```sh
# Recommended 6-skill starter pack archive and checksum manifest
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-0.2.0-starter.tar
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/SHA256SUMS

# Single skill archive and tarball checksum manifest
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/repo-audit-concurrency.tar.gz
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/checksums.sha256

# Complete audit suite archive (loads all 57 skills; see Stage-1 budget warning above)
curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-suite.tar.gz
```

### Verify integrity and build provenance

Verify cryptographic checksums and build provenance before extracting:

```sh
# Verify SHA-256 checksums for downloaded archives
sha256sum --ignore-missing -c SHA256SUMS
sha256sum --ignore-missing -c checksums.sha256

# Verify SLSA build provenance attestation using GitHub CLI
gh attestation verify fred-skills-0.2.0-starter.tar --repo fderuiter/fred-skills
gh attestation verify repo-audit-concurrency.tar.gz --repo fderuiter/fred-skills
gh attestation verify fred-skills-suite.tar.gz --repo fderuiter/fred-skills
```

### Install into an agent environment

Extract the verified archive directly into your agent's skills directory:

```sh
mkdir -p /path/to/project/.agents/skills/

# Extract recommended 6-skill starter pack
tar -xf fred-skills-0.2.0-starter.tar -C /path/to/project/.agents/skills/

# Or extract a single specialist (creates repo-audit-concurrency/ directory)
tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/

# Extract complete suite (creates all 57 skill directories; see Stage-1 budget warning above)
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
