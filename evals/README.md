# Executable evaluation harness

`cases.json` selects 15 raw cases across concurrency, authorization, transactions,
test oracles, and synthesis: two relevant/control inputs and an unrelated routing task
per domain. The original 99 specialist scenario specifications are separate authoring
inputs, not 99 executable cases or completed model evaluations.

The harness creates a fresh case directory for each configuration/repeat, copies only
raw `source/` and (for `with_skill`) one standalone `skill/`, writes a user request,
executes an explicit adapter argv without a shell, then grades outside that workdir.
Expected keys are not placed in the prompt or copied source. This reduces accidental
answer leakage; the process still has normal filesystem/network access. Use an external
sandbox for untrusted adapters and never grant them access to private data unnecessarily.

## Adapter contract

Your adapter receives placeholders `{prompt_file}`, `{output_file}`, and `{case_dir}`.
It should invoke your chosen agent in a fresh session, preserve its actual output/trace,
and write JSON at the output file:

```json
{
  "actor": {"kind": "agent", "model": "actual-model", "host": "actual-host", "version": "actual-version"},
  "findings": [{"key": "candidate-contract", "status": "supported", "evidence": "source path, trace, and relevant safeguard"}],
  "usage": {"tokens": 1234}
}
```

Use actual model/host identifiers and measured usage; omit unknown tokens. Allowed status
values: `confirmed`, `supported`, `needs-validation`, `hardening`, `refuted`, `grouped`.
Only confirmed/supported/grouped keys are actionable for the bounded rubric. An empty
array is valid. Do not give expected answers or other case results to the agent.

For a cross-platform harness self-check from the repository root:

```python
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
from fredlib.evaluations import evaluate
result = evaluate(Path("audit-output/evaluation"), [
    sys.executable, str(Path("evals/reference_adapter.py").resolve()),
    "--prompt", "{prompt_file}", "--output", "{output_file}"
])
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
