# Executable evaluation harness

`cases.json` selects 171 raw cases across all 57 skills spanning all six suites
(`repository-audits`, `software-architecture`, `code-review`, `automated-testing`,
`spec-driven-development`, and `repository-workflows`): relevant failure, safe control,
and routing tasks per skill. The 171 skill scenario specifications in
`references/evaluation-scenarios.json` remain separate authoring inputs, not 171
completed model evaluations.

The harness creates a fresh case directory for each configuration/repeat, copies only
raw `source/` and (for `with_skill`) one standalone `skill/` (excluding `__pycache__`),
writes a blind user request without schema filenames or required-field hints, executes an
explicit adapter argv without a shell, then grades outside that workdir. Expected anchors,
expected assertions, and `writable_paths` rules are never placed in `prompt.md` or the
copied workspace. `./source` is read-only by default and permits modifications only inside
declared `writable_paths` globs for source-mutating skills. This reduces accidental answer
leakage; the process still has normal filesystem/network access. Use an external sandbox
for untrusted adapters and never grant them access to private data unnecessarily.

## Adapter contract

Your adapter receives placeholders `{prompt_file}`, `{output_file}`, and `{case_dir}`.
It should invoke your chosen agent in a fresh session, preserve its actual output/trace,
and write a suite-valid JSON deliverable (either unwrapped alongside optional `actor` and
`usage`, or nested under `deliverable`) at the output file:

```json
{
  "actor": {"kind": "agent", "model": "actual-model", "host": "actual-host", "version": "actual-version"},
  "deliverable": {
    "schema_version": "1.0"
  },
  "usage": {"tokens": 1234}
}
```

Use actual model/host identifiers and measured usage; omit unknown tokens. Deliverables
are validated against canonical suite and workflow schemas (`audit.schema.json`,
`architecture.schema.json`, `review.schema.json`, `testing.schema.json`,
`spec.schema.json`, and workflow companion schemas) plus all six suite semantic
validators (`validate_audit.py` through `validate_workflow.py`). Allowed audit finding
statuses are `confirmed`, `supported`, `needs-validation`, `hardening`, and `refuted`;
only `confirmed` and `supported` audit findings are actionable. Grading matches structural
property anchors (such as `(topic, root_cause, locations)` fingerprints on audit ledgers)
rather than arbitrary run-local IDs. Pass `--max-tokens` on `fred.py evaluate` (or
`max_tokens` in Python) to fail runs whose reported `usage.tokens` exceeds your budget.
Do not give expected answers or other case results to the agent.

For a cross-platform harness self-check from the repository root:

```python
from pathlib import Path
import sys

sys.path.insert(0, "scripts")
from fredlib.evaluations import evaluate

result = evaluate(
    Path("audit-output/evaluation"),
    [
        sys.executable,
        str(Path("evals/reference_adapter.py").resolve()),
        "--prompt",
        "{prompt_file}",
        "--output",
        "{output_file}",
    ],
)
print(result["summary"])
```

`reference_adapter.py` is a deterministic fixture adapter. It deliberately does not use
an LLM or attribute its result to the skill. Equal perfect scores in both configurations
check harness plumbing and the bounded keys, not skill effectiveness. Replace the adapter
with a reviewed real agent wrapper to measure model behavior. The CLI also accepts a JSON
array via `--adapter-argv`; shell quoting depends on your platform.

## Results and limits

`benchmark.json` records toolkit version, manifest digest, adapter argv, case/configuration,
repeat, duration, actor/usage when supplied, errors, and rubric results. Each run preserves
prompt, result, stdout/stderr logs, and grading. Nonzero exit, timeout, missing/malformed
result, source mutation, and results larger than 2 MB invalidate a run. Logs stream to disk
without a disk quota. POSIX timeouts stop the process group; Windows stops the direct child
only. Child management and host/model limits belong in your adapter or external sandbox.

The exact-match rubric requires expected keys and nonempty supporting text, but does not
understand whether that text proves the claim. Review raw evidence, false positives,
uncertainty, source freshness, repeated outcomes, and independent controls. Runtime and
token counts alone cannot establish quality. Never run these adapters in ordinary CI
with live model credentials or turn fixture self-checks into a quality badge.
