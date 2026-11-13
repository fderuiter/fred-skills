from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import time

from .common import ROOT, atomic_json, digest, package_files, read_json, relative_path, version


def evaluate(output: Path, command: list[str] | None = None, cases: list[str] | None = None,
             repeats: int = 1, timeout: int = 120, root: Path = ROOT) -> dict:
    """Run a user-supplied adapter without a shell; never pass grader answers to it."""
    if not command or not 1 <= repeats <= 10 or not 1 <= timeout <= 3600:
        raise ValueError("Evaluation requires an explicit JSON argv adapter, 1–10 repeats, and a bounded timeout")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new or empty evaluation output directory")
    manifest = read_json(root / "evals/cases.json")
    selected = [case for case in manifest["cases"] if not cases or case["id"] in cases]
    if cases and set(cases) - {case["id"] for case in selected}:
        raise ValueError("Unknown evaluation case ID")
    results = []
    for case in selected:
        for configuration in ["without_skill", "with_skill"]:
            for repeat in range(repeats):
                work = output / case["id"] / configuration / str(repeat + 1)
                work.mkdir(parents=True)
                fixture = root / "evals" / relative_path(case["fixture"])
                shutil.copytree(fixture, work / "source")
                if configuration == "with_skill":
                    skill = root / "skills" / relative_path(case["skill"])
                    shutil.copytree(skill, work / "skill", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                request = case["prompt"] + "\n\nInspect ./source. Return result.json with a findings array of {key, status, evidence} records."
                if configuration == "with_skill":
                    request += "\nA skill is available at ./skill/SKILL.md. Use it only if it applies to the user's request."
                request += "\nInspect only this case's source and optional skill; do not inspect grading data, parent workspaces, or other cases."
                prompt = work / "prompt.md"
                prompt.write_text(request + "\n", encoding="utf-8")
                inputs_before = package_files(work / "source")
                substitutions = {"{prompt_file}": str(prompt), "{output_file}": str(work / "result.json"), "{case_dir}": str(work)}
                argv = []
                for argument in command:
                    for key, value in substitutions.items():
                        argument = argument.replace(key, value)
                    argv.append(argument)
                start = time.monotonic()
                state, result, error = "executed", None, None
                try:
                    # Stream logs to disk rather than retaining arbitrary adapter output in memory.
                    with (work / "stdout.log").open("wb") as stdout, (work / "stderr.log").open("wb") as stderr:
                        proc = subprocess.Popen(argv, cwd=work, stdout=stdout, stderr=stderr,
                                                start_new_session=os.name == "posix")
                        try:
                            proc.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            if os.name == "posix":
                                try:
                                    os.killpg(proc.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                            else:
                                proc.kill()
                            proc.wait()
                            raise
                    if proc.returncode:
                        state, error = "failed", f"Adapter exited {proc.returncode}"
                    elif (work / "result.json").exists():
                        if (work / "result.json").stat().st_size > 2_000_000:
                            raise ValueError("Adapter result exceeds the 2 MB limit")
                        result = read_json(work / "result.json")
                        if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
                            raise ValueError("Adapter result must be an object with a findings array")
                    else:
                        state, error = "failed", "Adapter did not write result.json"
                except subprocess.TimeoutExpired:
                    state, error = "timeout", "Adapter exceeded its time budget"
                except (OSError, ValueError) as exc:
                    state, error = "failed", str(exc)
                try:
                    inputs_changed = inputs_before != package_files(work / "source")
                except (OSError, ValueError):
                    inputs_changed = True
                if inputs_changed:
                    state, error = "failed", "Adapter modified the read-only evaluation source"
                # Grading happens only after adapter execution, outside its workdir.
                expected = set(case["expected_keys"])
                items = result.get("findings", []) if isinstance(result, dict) else []
                malformed = not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("key"), str) or item.get("status") not in {"confirmed", "supported", "needs-validation", "hardening", "refuted", "grouped"} for item in items)
                if malformed:
                    items, state, error = [], "failed", "Malformed findings response"
                reported = {item["key"] for item in items if item.get("status") in {"confirmed", "supported", "grouped"}}
                grounded = {item["key"] for item in items if isinstance(item.get("evidence"), str) and item["evidence"].strip()}
                true = reported & expected
                usage = result.get("usage") if isinstance(result, dict) else None
                tokens = usage.get("tokens") if isinstance(usage, dict) else None
                if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                    tokens = None
                record = {"case": case["id"], "configuration": configuration, "repeat": repeat + 1,
                    "state": state, "error": error, "seconds": round(time.monotonic() - start, 4),
                    "inputs_changed": inputs_changed,
                    "true_positives": len(true), "false_positives": len(reported - expected),
                    "false_negatives": len(expected - reported), "unsubstantiated_claims": len(reported - grounded),
                    "uncertain_count": sum(item.get("status") == "needs-validation" for item in items),
                    "actor": result.get("actor", {"kind": "unknown"}) if isinstance(result, dict) else {"kind": "unknown"},
                    "exact_match": state == "executed" and reported == expected and reported <= grounded,
                    "tokens": tokens,
                    "result_path": (work / "result.json").relative_to(output).as_posix()}
                results.append(record)
                atomic_json(work / "grading.json", record)
    summary = {}
    for configuration in ["without_skill", "with_skill"]:
        rows = [row for row in results if row["configuration"] == configuration]
        summary[configuration] = {"runs": len(rows), "exact_match_rate": sum(row["exact_match"] for row in rows) / len(rows) if rows else None,
                                  "mean_seconds": statistics.mean(row["seconds"] for row in rows) if rows else None,
                                  "failures": sum(row["state"] != "executed" for row in rows)}
    benchmark = {"schema_version": "1.0", "toolkit_version": version(root), "adapter_argv": command,
                 "cases_digest": digest(manifest), "runs": results, "summary": summary,
                 "limitations": ["Key matching is a bounded deterministic rubric, not semantic proof; review raw evidence and traces.",
                    "The adapter has normal process access. A fresh directory prevents accidental expected-answer leakage, not a security sandbox.",
                    "Log files are streamed without a disk quota. POSIX timeouts kill the process group; Windows stops the direct child only. Run untrusted adapters in an external sandbox with resource limits.",
                    "The provided deterministic reference adapter tests this harness, not an LLM. Label model/host versions in real adapter output."]}
    atomic_json(output / "benchmark.json", benchmark)
    return benchmark
