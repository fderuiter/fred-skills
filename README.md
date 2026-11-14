# fred-skills

Portable engineering workflows for investigating repositories, planning useful fixes,
and verifying that those fixes work. **41 standalone skills**: 33 audit domains,
a coordinator, findings synthesis, and six workflows from finding verification to
remediation reconciliation. All packages currently have experimental maturity.

Start with a task in the [task guide](docs/experience.md), browse the
[searchable offline explorer](docs/explorer.html) after downloading the repository,
or choose a package from the [catalog](docs/catalog.md).

| I want to… | Start here |
| --- | --- |
| Review a pull request | `review-pr` with an explicit base revision |
| Investigate a bug | `investigate-bug` and a bounded critical workflow |
| Understand hidden contracts | `understand-repo` or `repo-audit-program` |
| Turn findings into a practical plan | `plan-findings` |
| Correct and independently verify a defect | `apply-fix`, then `verify-fix` |
| Prepare a release | `release-ready` |

## Installation

Install all 35 repository audit skills with a single marketplace command:

### Claude Code

```sh
/plugin marketplace add fderuiter/fred-skills
```

### Codex

```sh
codex plugin marketplace add fderuiter/fred-skills
```

Manual directory copying and local installer workflows (`scripts/fred.py install`) remain supported for environments without marketplace access.

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

The managed installer supports explicit Codex, Claude Code, Antigravity, and Gemini CLI
locations, with dry runs, local-change protection, receipts, backups, and rollback.
Copy-path checks do not establish behavior on those hosts. Manual directory copying
remains supported; Python is unnecessary for instruction-only use.

See the [CLI guide](docs/toolkit.md) for resuming work, source freshness, reports,
comparisons, scanner imports, project profiles, and installation recovery. See
[usage](docs/usage.md) for portable invocation and [evaluations](evals/README.md) for
baseline/with-skill adapters, raw fixtures, and grading limits.

## Trust and validation

Findings retain intended behavior, source evidence, safe controls, uncertainty, and
actual validation. Remediation status is recorded separately from evidence certainty.
A finding absent from a later run is not automatically fixed. Local tools have no
telemetry or built-in network calls; your selected agent/adapter has its own behavior.

The [validation record](docs/validation.md) distinguishes deterministic tests, sampled
agent runs, and unexecuted scenario specifications. Host behavior and broad model
quality are not certified. The [implementation status](docs/implementation-status.md)
records delivered features and remaining validation or owner decisions.

For maintenance, see [CONTRIBUTING.md](CONTRIBUTING.md),
[architecture](docs/architecture.md), [security](SECURITY.md), and
[research](docs/research.md). [Licensing](docs/licensing.md) requires an owner decision
before public release publication; this PR does not select a license or publish a tag.
