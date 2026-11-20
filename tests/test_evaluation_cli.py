from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import evaluations
from validate_skills import validate_skill

from tests.catalog_oracles import (
    expected_eval_case_count,
    expected_non_audit_eval_run_count,
    expected_non_audit_skill_count,
    expected_paired_eval_run_count,
    expected_routing_eval_case_count,
    expected_suite_names,
    expected_total_skills,
)


class EvaluationTests(unittest.TestCase):
    def test_reference_adapter_exercises_all_cases_without_claiming_model_quality(self):
        argv = [
            sys.executable,
            str(ROOT / "evals/reference_adapter.py"),
            "--prompt",
            "{prompt_file}",
            "--output",
            "{output_file}",
        ]
        expected_cases = expected_eval_case_count(ROOT)
        expected_skills = expected_total_skills(ROOT)
        expected_non_audit = expected_non_audit_skill_count(ROOT)
        expected_runs = expected_paired_eval_run_count(ROOT)
        expected_non_audit_runs = expected_non_audit_eval_run_count(ROOT)
        expected_suites = expected_suite_names(ROOT)
        self.assertGreaterEqual(expected_cases, 1)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_non_audit, 1)
        self.assertGreaterEqual(expected_runs, 1)
        self.assertGreaterEqual(expected_non_audit_runs, 1)
        self.assertGreaterEqual(len(expected_suites), 1)

        manifest = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
        self.assertEqual(expected_cases, len(manifest["cases"]))
        skills_covered = {case["skill"] for case in manifest["cases"]}
        self.assertEqual(expected_skills, len(skills_covered))

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        skill_to_suite = {
            skill: suite["name"]
            for suite in suites_doc["suites"]
            for phase in suite["phases"]
            for skill in phase["skills"]
        }
        self.assertEqual(set(skill_to_suite.keys()), skills_covered)
        suites_covered = {skill_to_suite[skill] for skill in skills_covered}
        self.assertEqual(expected_suites, suites_covered)
        non_audit_skills = {
            s for s, st in skill_to_suite.items() if st != "repository-audits"
        }
        self.assertEqual(expected_non_audit, len(non_audit_skills))
        self.assertTrue(non_audit_skills.issubset(skills_covered))

        leaked_arbitrary_ids = (
            "ADR-001",
            "DIFF-001",
            "TC-BURST-LIMIT",
            "DEC-RETRY-001",
            "CHK-SCOPE-001",
            "F-101",
            "F-401",
            "B-001",
        )
        for case in manifest["cases"]:
            raw_prompt = str(case["prompt"])
            for leaked_id in leaked_arbitrary_ids:
                self.assertNotIn(
                    leaked_id, raw_prompt, f"{case['id']} leaked {leaked_id}"
                )
            for wglob in case.get("writable_paths", []):
                self.assertNotIn(
                    wglob, raw_prompt, f"{case['id']} leaked writable glob {wglob}"
                )
            for anchor in case.get("expected_anchors", []):
                if isinstance(anchor, dict) and "root_cause" in anchor:
                    self.assertNotIn(
                        str(anchor["root_cause"]),
                        raw_prompt,
                        f"{case['id']} leaked root_cause {anchor['root_cause']}",
                    )

        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            result = evaluations.evaluate(temp_path, argv)
            self.assertEqual(expected_runs, len(result["runs"]))
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"]
                    for row in result["runs"]
                ),
                [
                    (row["case"], row["configuration"], row["state"], row.get("error"))
                    for row in result["runs"]
                    if row["state"] != "executed" or not row["exact_match"]
                ][:10],
            )
            self.assertTrue(
                all(
                    row["actor"]["kind"] == "deterministic-fixture-adapter"
                    for row in result["runs"]
                )
            )

            # Verify all non-audit skills recorded state=="executed" and exact_match==True
            case_to_skill = {c["id"]: c["skill"] for c in manifest["cases"]}
            non_audit_runs = [
                row
                for row in result["runs"]
                if case_to_skill[row["case"]] in non_audit_skills
            ]
            self.assertEqual(expected_non_audit_runs, len(non_audit_runs))
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"] is True
                    for row in non_audit_runs
                )
            )

            # Verify prompts never leak grading expectations, schema filenames, field hints, or writable_paths
            forbidden_without_skill_tokens = (
                ".schema.json",
                "schema_version",
                "interview_decisions",
                "convergence_checks",
                "remediation_state_transitions",
                "task_breakdowns",
            )
            for case in manifest["cases"]:
                for cfg in ("without_skill", "with_skill"):
                    prompt_text = (
                        temp_path / case["id"] / cfg / "1/prompt.md"
                    ).read_text(encoding="utf-8")
                    self.assertNotIn("expected_keys", prompt_text)
                    self.assertNotIn("expected_anchors", prompt_text)
                    self.assertNotIn("expected_assertions", prompt_text)
                    self.assertNotIn("writable_paths", prompt_text)
                    for wglob in case.get("writable_paths", []):
                        self.assertNotIn(wglob, prompt_text)
                    if cfg == "without_skill":
                        for tok in forbidden_without_skill_tokens:
                            self.assertNotIn(
                                tok,
                                prompt_text,
                                f"{case['id']} without_skill prompt leaked {tok!r}",
                            )

            self.assertFalse(
                (temp_path / "concurrency-a/without_skill/1/skill").exists()
            )
            self.assertTrue(
                (temp_path / "concurrency-a/with_skill/1/skill/SKILL.md").exists()
            )

            # Reference adapter reports usage.tokens on every run
            for cfg in ("without_skill", "with_skill"):
                self.assertEqual(
                    expected_cases, result["summary"][cfg]["tokens_reported_runs"]
                )
                self.assertIsNotNone(result["summary"][cfg]["total_tokens"])
                self.assertGreater(result["summary"][cfg]["total_tokens"], 0)
                self.assertIsNotNone(result["summary"][cfg]["mean_tokens"])
            lift = result["summary"]["skill_lift"]
            self.assertEqual(expected_cases, lift["paired_runs"])
            self.assertEqual(0.0, lift["exact_match_rate_delta"])
            self.assertEqual(expected_cases, lift["paired_token_runs"])
            self.assertIsNotNone(lift["total_tokens_delta"])
            self.assertIsNotNone(lift["mean_tokens_delta"])

    def test_deliverable_wrapper_and_strict_schema_validation_for_non_audit_contracts(
        self,
    ):
        wrapped_script = (
            "import json\n"
            "payload = {\n"
            "  'actor': {'kind': 'wrapper-test'},\n"
            "  'usage': {'tokens': 200},\n"
            "  'deliverable': {\n"
            "    'schema_version': '1.0',\n"
            "    'title': 'Boundary spec',\n"
            "    'status': 'accepted',\n"
            "    'boundaries': [],\n"
            "    'decisions': []\n"
            "  }\n"
            "}\n"
            "open('result.json', 'w', encoding='utf-8').write(json.dumps(payload))\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            res = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", wrapped_script],
                cases=["arch-boundary-specification-b"],
            )
            self.assertTrue(
                all(
                    row["state"] == "executed"
                    and row["exact_match"]
                    and row["tokens"] == 200
                    for row in res["runs"]
                )
            )

        invalid_extra_prop_script = (
            "import json\n"
            "payload = {\n"
            "  'schema_version': '1.0',\n"
            "  'verdict': 'approve',\n"
            "  'summary': 'ok',\n"
            "  'findings': [],\n"
            "  'unexpected_field': True\n"
            "}\n"
            "open('result.json', 'w', encoding='utf-8').write(json.dumps(payload))\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            res_bad = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", invalid_extra_prop_script],
                cases=["review-diff-analysis-b"],
            )
            self.assertTrue(
                all(
                    row["state"] == "failed" and not row["exact_match"]
                    for row in res_bad["runs"]
                )
            )

    def test_writable_paths_allows_scoped_edits_and_blocks_unapproved_files(self):
        mutating_scenarios = [
            (
                "spec-authoring-a",
                "source/SPEC.md",
                "# Spec\n",
                "source/exporter.py",
                {
                    "schema_version": "1.0",
                    "title": "Tenant Export Streaming Specification",
                    "status": "approved",
                    "summary": "Approved specification for cursor-paginated NDJSON export.",
                    "interview_decisions": [
                        {
                            "id": "DEC-EXPORT-001",
                            "category": "architecture",
                            "question": "How should pagination work?",
                            "decision": "Cursor-based streaming.",
                            "rationale": "Avoids unbounded memory.",
                        }
                    ],
                    "requirements": [
                        {
                            "id": "REQ-001",
                            "description": "Stream NDJSON batches bounded by 500 rows.",
                            "priority": "must",
                        }
                    ],
                    "new_file_scopes": ["exporter.py"],
                    "out_of_scope": ["Multi-tenant exports"],
                    "verification_commands": ["python -m unittest discover -s tests"],
                    "convergence_checks": [],
                },
            ),
            (
                "repo-apply-fix-a",
                "source/retry_budget.py",
                "def can_retry(a: int, m: int) -> bool:\n    return 0 <= a < m\n",
                "source/readonly_policy.py",
                {
                    "file_changes": [{"path": "retry_budget.py", "action": "modified"}],
                    "diff_summaries": ["Added upper bound check"],
                    "execution_results": [
                        {"command": "pytest", "exit_code": 0, "passed": True}
                    ],
                    "compatibility_limits": ["None"],
                },
            ),
            (
                "repo-specify-regression-a",
                "source/tests/test_quota_regression_spec.py",
                "def check_quota() -> None:\n    assert True\n",
                "source/quota_ledger.py",
                {
                    "finding_id": "F-201",
                    "test_file": "tests/test_quota_regression_spec.py",
                    "failure_boundaries": [
                        "quota_ledger.debit_quota(available=10, requested=25)"
                    ],
                    "test_command_traces": [
                        {
                            "command": "python -m unittest tests.test_quota_regression_spec",
                            "exit_code": 1,
                            "output": "AssertionError: ValueError not raised",
                        }
                    ],
                    "assertion_expectations": [
                        {
                            "description": "Overdraft exceeding available quota must raise ValueError",
                            "expected": "ValueError('insufficient quota')",
                            "actual": "-15",
                        }
                    ],
                },
            ),
            (
                "test-case-generation-a",
                "source/tests/test_rate_limiter_generated.py",
                "def check_rate_limiter() -> None:\n    assert True\n",
                "source/rate_limiter.py",
                {
                    "schema_version": "1.0",
                    "component": "rate_limiter",
                    "framework": "unittest",
                    "test_cases": [
                        {
                            "id": "TC-BURST-LIMIT",
                            "name": "test_burst_limit_boundary",
                            "description": "tests/test_rate_limiter_generated.py: assert exact burst limit.",
                            "oracle_type": "boundary",
                            "verified": True,
                        }
                    ],
                },
            ),
        ]

        for (
            case_id,
            allowed_rel,
            allowed_body,
            forbidden_rel,
            deliverable,
        ) in mutating_scenarios:
            with self.subTest(case=case_id, mode="allowed"):
                ok_script = (
                    "import json\n"
                    "from pathlib import Path\n"
                    f"target = Path({allowed_rel!r})\n"
                    "target.parent.mkdir(parents=True, exist_ok=True)\n"
                    f"target.write_text({allowed_body!r}, encoding='utf-8')\n"
                    f"Path('result.json').write_text(json.dumps({deliverable!r}), encoding='utf-8')\n"
                )
                with tempfile.TemporaryDirectory() as temp:
                    res_ok = evaluations.evaluate(
                        Path(temp),
                        [sys.executable, "-c", ok_script],
                        cases=[case_id],
                    )
                    self.assertTrue(
                        all(
                            row["inputs_changed"]
                            and row["state"] == "executed"
                            and row["exact_match"]
                            for row in res_ok["runs"]
                        )
                    )

            with self.subTest(case=case_id, mode="unapproved"):
                bad_script = (
                    "import json\n"
                    "from pathlib import Path\n"
                    f"Path({forbidden_rel!r}).write_text('# unauthorized edit\\n', encoding='utf-8')\n"
                    f"Path('result.json').write_text(json.dumps({deliverable!r}), encoding='utf-8')\n"
                )
                with tempfile.TemporaryDirectory() as temp:
                    res_bad = evaluations.evaluate(
                        Path(temp),
                        [sys.executable, "-c", bad_script],
                        cases=[case_id],
                    )
                    self.assertTrue(
                        all(
                            row["inputs_changed"]
                            and row["state"] == "failed"
                            and not row["exact_match"]
                            for row in res_bad["runs"]
                        )
                    )

    def _make_audit_finding(self, fid: str = "F-1") -> dict[str, object]:
        return {
            "id": fid,
            "topic": "concurrency",
            "tags": ["concurrency", "stale-publication"],
            "title": "Unconditional completion assignment publishes stale generation value",
            "status": "supported",
            "severity": "high",
            "confidence": "high",
            "contract_ids": ["C-1"],
            "evidence_ids": ["EV-1"],
            "preconditions": "Concurrent Session.start calls overlap.",
            "sequence": [
                "Caller starts generation 1 and generation 2.",
                "Generation 1 resolves second and overwrites active session.",
            ],
            "expected": "Only latest generation updates Session state.",
            "actual": "Older promise overwrites newer generation state.",
            "impact": "Publishes stale session state.",
            "root_cause": "stale-publication",
            "counterevidence": "No caller generation check guards Session.start.",
            "remedy": "Check generation equality after await.",
            "acceptance": ["Regression test verifies stale generation is ignored."],
            "validation": {
                "state": "not-run",
                "commands": [],
                "result": "Static inspection completed.",
            },
            "related_findings": [],
        }

    def _make_audit_ledger(
        self,
        *,
        findings: list[dict[str, object]] | None = None,
        evidence_path: str = "session.mjs",
        symbol: str = "Session.start",
        unknown_evidence_ref: bool = False,
    ) -> dict[str, object]:
        rows = findings or []
        ev_id = "EV-MISSING" if unknown_evidence_ref else "EV-1"
        evidence = [
            {
                "id": "EV-1",
                "path": evidence_path,
                "line_start": 1,
                "line_end": 8,
                "symbol": symbol,
                "context": f"{evidence_path}:{symbol} inspected.",
            }
        ]
        contracts = (
            [
                {
                    "id": "C-1",
                    "dimension": "concurrency",
                    "scope": "session.mjs",
                    "intended": "Session.start preserves latest generation.",
                    "observed": "Unconditional completion overwrites state.",
                    "enforcement": "missing-guard",
                    "evidence_ids": [ev_id],
                }
            ]
            if rows
            else []
        )
        return {
            "schema_version": "1.0",
            "run_id": "eval-concurrency-test",
            "repository": {
                "identity": "eval-fixture",
                "revision": "0000000",
                "dirty": False,
                "snapshot": "session.mjs",
            },
            "scope": ["session.mjs"],
            "coverage": [
                {
                    "topic": "concurrency",
                    "scope": "session.mjs",
                    "status": "inspected",
                    "evidence_ids": ["EV-1"],
                    "notes": "Inspected session.mjs.",
                    "next_action": "none",
                }
            ],
            "contracts": contracts,
            "evidence": evidence,
            "findings": rows,
            "issues": [],
            "limitations": ["Synthetic fixture scope."],
            "checks": [],
        }

    def test_summary_reports_tokens_and_paired_skill_lift(self):
        positive_ledger = self._make_audit_ledger(
            findings=[self._make_audit_finding("F-1")]
        )
        empty_ledger = self._make_audit_ledger(findings=[])
        lift_script = (
            "import json\n"
            "from pathlib import Path\n"
            "has_skill = Path('skill/SKILL.md').is_file()\n"
            f"deliverable = {positive_ledger!r} if has_skill else {empty_ledger!r}\n"
            "tokens = 420 if has_skill else 300\n"
            "Path('result.json').write_text(json.dumps({'deliverable': deliverable, 'usage': {'tokens': tokens}}), encoding='utf-8')\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            bench = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", lift_script],
                cases=["concurrency-a"],
                repeats=2,
            )
            without_summary = bench["summary"]["without_skill"]
            with_summary = bench["summary"]["with_skill"]
            lift = bench["summary"]["skill_lift"]

            self.assertEqual(2, without_summary["tokens_reported_runs"])
            self.assertEqual(600, without_summary["total_tokens"])
            self.assertEqual(300, without_summary["mean_tokens"])
            self.assertEqual(0.0, without_summary["exact_match_rate"])

            self.assertEqual(2, with_summary["tokens_reported_runs"])
            self.assertEqual(840, with_summary["total_tokens"])
            self.assertEqual(420, with_summary["mean_tokens"])
            self.assertEqual(1.0, with_summary["exact_match_rate"])

            self.assertEqual(2, lift["paired_runs"])
            self.assertEqual(1.0, lift["exact_match_rate_delta"])
            self.assertEqual(2, lift["true_positives_delta"])
            self.assertEqual(-2, lift["false_negatives_delta"])
            self.assertEqual(2, lift["paired_token_runs"])
            self.assertEqual(240, lift["total_tokens_delta"])
            self.assertEqual(120, lift["mean_tokens_delta"])

    def test_copied_skill_excludes_pycache_and_passes_validator(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp) / "repo"
            skill_src = ROOT / "skills/repo-audit-diagnostics"
            skill_dst = temp_root / "skills/repo-audit-diagnostics"
            skill_dst.parent.mkdir(parents=True)
            import shutil

            shutil.copytree(skill_src, skill_dst)
            pycache = skill_dst / "scripts/__pycache__"
            pycache.mkdir(parents=True, exist_ok=True)
            (pycache / "triage.cpython-312.pyc").write_bytes(b"\x00\x01")
            (temp_root / "VERSION").write_text("0.2.0\n", encoding="utf-8")
            (temp_root / "evals/fixtures/diag/a").mkdir(parents=True)
            (temp_root / "evals/fixtures/diag/a/note.txt").write_text(
                "raw\n", encoding="utf-8"
            )
            (temp_root / "evals/cases.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "diag-routing",
                                "fixture": "fixtures/diag/a",
                                "skill": "repo-audit-diagnostics",
                                "prompt": "Return empty findings.",
                                "expected_keys": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            out_dir = Path(temp) / "out"
            evaluations.evaluate(
                out_dir,
                [
                    sys.executable,
                    "-c",
                    "open('result.json', 'w', encoding='utf-8').write('{\"findings\": []}')",
                ],
                root=temp_root,
            )
            copied_skill = out_dir / "diag-routing/with_skill/1/skill"
            self.assertFalse((copied_skill / "scripts/__pycache__").exists())
            named_copy = Path(temp) / "check/repo-audit-diagnostics"
            shutil.copytree(copied_skill, named_copy)
            self.assertEqual([], validate_skill(named_copy))

    def test_missing_adapter_result_is_a_failed_run(self):
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(
                Path(temp), [sys.executable, "-c", "pass"], cases=["concurrency-b"]
            )
            self.assertTrue(
                all(
                    row["state"] == "failed" and not row["exact_match"]
                    for row in result["runs"]
                )
            )

    def test_timeout_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", "import time; time.sleep(2)"],
                cases=["concurrency-b"],
                timeout=1,
            )
            self.assertTrue(all(row["state"] == "timeout" for row in result["runs"]))

    def test_malformed_usage_does_not_abort_and_missing_findings_fails(self):
        empty_ledger = self._make_audit_ledger(findings=[])
        with tempfile.TemporaryDirectory() as temp:
            adapter = [
                sys.executable,
                "-c",
                f"import json; json.dump({{'deliverable': {empty_ledger!r}, 'usage': None}}, open('result.json', 'w'))",
            ]
            result = evaluations.evaluate(
                Path(temp), adapter, cases=["concurrency-routing"]
            )
            self.assertTrue(all(row["tokens"] is None for row in result["runs"]))
            self.assertTrue(all(row["state"] == "executed" for row in result["runs"]))
            for cfg in ("without_skill", "with_skill"):
                self.assertEqual(0, result["summary"][cfg]["tokens_reported_runs"])
                self.assertIsNone(result["summary"][cfg]["total_tokens"])
                self.assertIsNone(result["summary"][cfg]["mean_tokens"])
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", "open('result.json', 'w').write('{}')"],
                cases=["concurrency-routing"],
            )
            self.assertTrue(all(row["state"] == "failed" for row in result["runs"]))

    def test_source_mutation_invalidates_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('source/injected').write_text('changed'); Path('result.json').write_text('{\"findings\": []}')",
            ]
            result = evaluations.evaluate(
                Path(temp), adapter, cases=["concurrency-routing"]
            )
            self.assertTrue(
                all(
                    row["inputs_changed"] and row["state"] == "failed"
                    for row in result["runs"]
                )
            )

    def test_cli_discovery_and_error_paths(self):
        for args in [["packs"], ["tasks"], ["hosts"], ["search", "stale response"]]:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/fred.py"), *args],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(json.loads(proc.stdout))
        bad = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/fred.py"),
                "evaluate",
                "--output",
                "unused",
                "--adapter-argv",
                "not-json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, bad.returncode)

    def test_cli_evaluate_projects_normalized_records_across_suite_schemas(self):
        argv = json.dumps(
            [
                sys.executable,
                str(ROOT / "evals/reference_adapter.py"),
                "--prompt",
                "{prompt_file}",
                "--output",
                "{output_file}",
            ]
        )
        sampled_cases = [
            "review-diff-analysis-a",
            "arch-decision-record-a",
            "test-gap-analysis-a",
            "spec-convergence-a",
            "repo-verify-finding-a",
            "session-handoff-a",
            "terse-output-commit-a",
        ]
        with tempfile.TemporaryDirectory() as temp:
            out_dir = Path(temp) / "bench"
            cmd = [
                sys.executable,
                str(ROOT / "scripts/fred.py"),
                "evaluate",
                "--output",
                str(out_dir),
                "--adapter-argv",
                argv,
            ]
            for cid in sampled_cases:
                cmd.extend(["--case", cid])
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            bench = json.loads((out_dir / "benchmark.json").read_text(encoding="utf-8"))
            self.assertEqual(14, len(bench["runs"]))
            for row in bench["runs"]:
                self.assertEqual("executed", row["state"])
                self.assertTrue(row["exact_match"])
                records = row["normalized_records"]
                self.assertTrue(
                    records, f"Expected normalized_records for {row['case']}"
                )
                for rec in records:
                    self.assertEqual({"key", "status", "evidence"}, set(rec.keys()))
                    self.assertTrue(rec["key"])
                    self.assertIn(rec["status"], evaluations._AUDIT_STATUSES)
                    self.assertTrue(rec["evidence"])

        public_exports = [
            name
            for name in dir(evaluations)
            if not name.startswith("_")
            and callable(getattr(evaluations, name))
            and getattr(evaluations, name).__module__ == evaluations.__name__
        ]
        self.assertEqual(["evaluate"], public_exports)

    def test_routing_prompts_use_domain_near_misses_without_artificial_hints(self):
        forbidden_hints = (
            "do not audit",
            "proofread",
            "sibling routing request",
            "abstain with",
            "return no audit findings",
        )
        expected_skills = expected_total_skills(ROOT)
        expected_routing = expected_routing_eval_case_count(ROOT)
        self.assertGreaterEqual(expected_skills, 1)
        self.assertGreaterEqual(expected_routing, 1)
        manifest = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
        routing_cases = [
            c for c in manifest["cases"] if str(c["id"]).endswith("-routing")
        ]
        self.assertEqual(expected_skills, expected_routing)
        self.assertEqual(expected_routing, len(routing_cases))
        for case in routing_cases:
            low = str(case["prompt"]).lower()
            for hint in forbidden_hints:
                self.assertNotIn(hint, low, f"{case['id']} leaked hint {hint!r}")

        scenario_files = sorted(
            (ROOT / "skills").glob("*/references/evaluation-scenarios.json")
        )
        self.assertEqual(expected_skills, len(scenario_files))
        for sf in scenario_files:
            doc = json.loads(sf.read_text(encoding="utf-8"))
            for sc in doc["scenarios"]:
                low = str(sc["prompt"]).lower()
                for hint in forbidden_hints:
                    self.assertNotIn(
                        hint, low, f"{sf.relative_to(ROOT)}:{sc['id']} leaked {hint!r}"
                    )

    def test_audit_schema_and_semantic_validator_reject_invalid_ledgers(self) -> None:
        valid_finding = self._make_audit_finding("F-999")
        invalid_payloads = [
            (
                "legacy-key-field",
                self._make_audit_ledger(
                    findings=[{**valid_finding, "key": "stale-publication"}]
                ),
            ),
            (
                "forbidden-category-field",
                self._make_audit_ledger(
                    findings=[{**valid_finding, "category": "concurrency"}]
                ),
            ),
            (
                "forbidden-grouped-status",
                self._make_audit_ledger(
                    findings=[{**valid_finding, "status": "grouped"}]
                ),
            ),
            (
                "confirmed-without-executed-command",
                self._make_audit_ledger(
                    findings=[{**valid_finding, "status": "confirmed"}]
                ),
            ),
            (
                "traversal-evidence-path",
                self._make_audit_ledger(
                    findings=[valid_finding],
                    evidence_path="../secret.mjs",
                ),
            ),
            (
                "dangling-evidence-reference",
                self._make_audit_ledger(
                    findings=[valid_finding],
                    unknown_evidence_ref=True,
                ),
            ),
        ]
        for label, bad_payload in invalid_payloads:
            with self.subTest(rejection=label), tempfile.TemporaryDirectory() as temp:
                script = f"import json; open('result.json', 'w', encoding='utf-8').write(json.dumps({bad_payload!r}))"
                res = evaluations.evaluate(
                    Path(temp),
                    [sys.executable, "-c", script],
                    cases=["concurrency-a"],
                )
                self.assertTrue(
                    all(
                        row["state"] == "failed" and not row["exact_match"]
                        for row in res["runs"]
                    ),
                    f"Expected failure for {label}: {res['runs']}",
                )

        good_payload = self._make_audit_ledger(findings=[valid_finding])
        with tempfile.TemporaryDirectory() as temp:
            script = f"import json; open('result.json', 'w', encoding='utf-8').write(json.dumps({good_payload!r}))"
            res_ok = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", script],
                cases=["concurrency-a"],
            )
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"]
                    for row in res_ok["runs"]
                )
            )

    def test_property_anchors_accept_arbitrary_ids_and_project_requirements_and_empty_handoff(
        self,
    ) -> None:
        arbitrary_scenarios: list[tuple[str, dict[str, object], str | None]] = [
            (
                "arch-decision-record-a",
                {
                    "schema_version": "1.0",
                    "title": "Outbox Relay Decision",
                    "status": "accepted",
                    "boundaries": [],
                    "decisions": [
                        {
                            "id": "ADR-1",
                            "title": "Adopt transactional outbox relay",
                            "context": "Dual writes drift when broker publish fails.",
                            "decision": "Commit outbox row atomically with order write.",
                            "consequences": [
                                "Requires relay polling worker.",
                                "Consumers handle idempotent delivery.",
                            ],
                        }
                    ],
                },
                None,
            ),
            (
                "review-diff-analysis-a",
                {
                    "schema_version": "1.0",
                    "verdict": "request_changes",
                    "summary": "Breaking signature change leaves invoice_client unupdated.",
                    "findings": [
                        {
                            "id": "FINDING-1",
                            "title": "Caller omits required currency argument",
                            "severity": "high",
                            "file": "invoice_client.py",
                            "line": 2,
                            "description": "invoice_client.py calls format_invoice(total) without currency.",
                            "recommendation": "Restore default currency or update all callers.",
                        }
                    ],
                },
                None,
            ),
            (
                "test-case-generation-a",
                {
                    "schema_version": "1.0",
                    "component": "rate_limiter",
                    "framework": "unittest",
                    "test_cases": [
                        {
                            "id": "TC-99",
                            "name": "test_arbitrary_id_boundary",
                            "description": "tests/test_rate_limiter_arbitrary.py: verify boundary.",
                            "oracle_type": "boundary",
                            "verified": True,
                        }
                    ],
                },
                "source/tests/test_rate_limiter_arbitrary.py",
            ),
            (
                "spec-interview-a",
                {
                    "schema_version": "1.0",
                    "title": "Webhook Retry Policy",
                    "status": "interviewing",
                    "summary": "Bounded webhook retry specification.",
                    "interview_decisions": [
                        {
                            "id": "DEC-1",
                            "category": "edge-case",
                            "question": "How many retries should run before dead-lettering?",
                            "decision": "Cap retries at 5 attempts with jitter.",
                            "rationale": "Bounds retry queue growth.",
                        }
                    ],
                    "requirements": [],
                    "new_file_scopes": ["webhooks/dispatcher.py"],
                    "out_of_scope": ["CLI flags"],
                    "verification_commands": ["pytest tests/test_dispatcher.py"],
                    "convergence_checks": [],
                },
                None,
            ),
            (
                "spec-convergence-a",
                {
                    "schema_version": "1.0",
                    "title": "Webhook Convergence",
                    "status": "diverged",
                    "summary": "Detects unapproved cli_flags.py drift.",
                    "interview_decisions": [],
                    "requirements": [],
                    "new_file_scopes": ["webhooks/dispatcher.py"],
                    "out_of_scope": ["cli_flags.py"],
                    "verification_commands": ["pytest tests/test_dispatcher.py"],
                    "convergence_checks": [
                        {
                            "id": "CHK-1",
                            "target": "cli_flags.py",
                            "status": "out-of-scope",
                            "evidence": "cli_flags.py violates specification scope boundaries.",
                        }
                    ],
                },
                None,
            ),
            (
                "repo-plan-fix-a",
                {
                    "task_breakdowns": [
                        {
                            "task_id": "TASK-A",
                            "title": "Remediate token store",
                            "file_scopes": ["token_store.py"],
                            "blocked_by": [],
                            "acceptance_criteria": [
                                "Verify token_store regression passes"
                            ],
                        },
                        {
                            "task_id": "TASK-B",
                            "title": "Remediate session gate",
                            "file_scopes": ["session_gate.py"],
                            "blocked_by": ["TASK-A"],
                            "acceptance_criteria": [
                                "Verify session_gate regression passes"
                            ],
                        },
                    ],
                    "dependency_order": ["TASK-A", "TASK-B"],
                    "file_scopes": ["token_store.py", "session_gate.py"],
                    "acceptance_criteria": ["All regressions pass"],
                },
                None,
            ),
            (
                "repo-verify-finding-a",
                {
                    "finding_id": "F-1",
                    "reproduction_status": "confirmed",
                    "safe_control_outcomes": [
                        {"control": "single-writer-control", "passed": True}
                    ],
                    "confidence_rating": "high",
                    "commands": ["python -c 'import ledger_writer'"],
                    "result": "Confirmed unsynchronized append on LedgerWriter.entries.",
                },
                None,
            ),
        ]
        for case_id, payload, extra_file in arbitrary_scenarios:
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as temp:
                file_setup = ""
                if extra_file:
                    file_setup = (
                        f"p = Path({extra_file!r})\n"
                        "p.parent.mkdir(parents=True, exist_ok=True)\n"
                        "p.write_text('def test_ok():\\n    assert True\\n', encoding='utf-8')\n"
                    )
                script = (
                    "import json\n"
                    "from pathlib import Path\n"
                    f"{file_setup}"
                    f"Path('result.json').write_text(json.dumps({payload!r}), encoding='utf-8')\n"
                )
                res = evaluations.evaluate(
                    Path(temp),
                    [sys.executable, "-c", script],
                    cases=[case_id],
                )
                self.assertTrue(
                    all(
                        row["state"] == "executed" and row["exact_match"]
                        for row in res["runs"]
                    ),
                    f"{case_id} failed with arbitrary IDs: {res['runs']}",
                )

        # Verify spec-authoring-a projects both interview_decisions and requirements
        spec_authoring_payload = {
            "schema_version": "1.0",
            "title": "Tenant Export Spec",
            "status": "approved",
            "summary": "Bounded NDJSON streaming specification.",
            "interview_decisions": [
                {
                    "id": "DEC-9",
                    "category": "architecture",
                    "question": "How should pagination work?",
                    "decision": "Cursor-based streaming.",
                    "rationale": "Bounds memory.",
                }
            ],
            "requirements": [
                {
                    "id": "REQ-9",
                    "description": "Stream NDJSON batches bounded at 500 rows.",
                    "priority": "must",
                }
            ],
            "new_file_scopes": ["exporter.py"],
            "out_of_scope": ["CSV export"],
            "verification_commands": ["python -m unittest"],
            "convergence_checks": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            script = (
                "import json\n"
                "from pathlib import Path\n"
                "Path('source/SPEC.md').write_text('# Spec\\n', encoding='utf-8')\n"
                f"Path('result.json').write_text(json.dumps({spec_authoring_payload!r}), encoding='utf-8')\n"
            )
            res_spec = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", script],
                cases=["spec-authoring-a"],
            )
            self.assertTrue(all(row["exact_match"] for row in res_spec["runs"]))
            self.assertEqual(2, len(res_spec["runs"][0]["normalized_records"]))

        # Verify session-handoff-b projects empty touched_files and rejects fabricated touched_files
        clean_handoff = {
            "schema_version": "1.0",
            "objective": "Inspect read-only status workspace",
            "repository": {"path": ".", "revision": "abc1234", "dirty": False},
            "verified_facts": ["Workspace is clean and read-only."],
            "assumptions": [],
            "completed_steps": ["Reviewed session_state.json"],
            "dead_ends": [],
            "touched_files": [],
            "untouched_scope": ["."],
            "commands_run": [{"command": "git status", "outcome": "passed"}],
            "next_action": "Confirm target milestone before editing files",
        }
        fabricated_handoff = {**clean_handoff, "touched_files": ["status.py"]}
        with tempfile.TemporaryDirectory() as temp:
            ok_script = f"import json; open('result.json', 'w', encoding='utf-8').write(json.dumps({clean_handoff!r}))"
            res_clean = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", ok_script],
                cases=["session-handoff-b"],
            )
            self.assertTrue(all(row["exact_match"] for row in res_clean["runs"]))
            self.assertEqual(
                [
                    {
                        "key": "untouched",
                        "status": "refuted",
                        "evidence": "untouched: Confirm target milestone before editing files",
                    }
                ],
                res_clean["runs"][0]["normalized_records"],
            )
        with tempfile.TemporaryDirectory() as temp:
            bad_script = f"import json; open('result.json', 'w', encoding='utf-8').write(json.dumps({fabricated_handoff!r}))"
            res_fab = evaluations.evaluate(
                Path(temp),
                [sys.executable, "-c", bad_script],
                cases=["session-handoff-b"],
            )
            self.assertTrue(all(not row["exact_match"] for row in res_fab["runs"]))

    def test_dual_schema_resolution_uses_canonical_suite_and_workflow_contracts(
        self,
    ) -> None:
        # Verify existing dual-schema skill repo-audit-diagnostics resolves audit.schema.json
        diag_path = evaluations._resolve_case_schema(
            {"skill": "repo-audit-diagnostics"}, "audit", ROOT
        )
        self.assertEqual("audit.schema.json", diag_path.name)

        # Verify scaffolded dual-schema skill where <skill>.schema.json sorts before architecture.schema.json
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp) / "repo"
            import shutil

            for rel in ("catalog", "shared", "scripts", "skills", "schemas", "VERSION"):
                src = ROOT / rel
                dst = temp_root / rel
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/scaffold_skill.py"),
                    "arch-aaa-dual",
                    "--suite",
                    "software-architecture",
                    "--phase",
                    "1",
                    "--description",
                    "Specify dual-schema architecture probes. Use when testing dual schema resolution. Do not use for production audits.",
                    "--catalog",
                    "--root",
                    str(temp_root),
                ],
                cwd=temp_root,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            schema_names = sorted(
                p.name
                for p in (temp_root / "skills/arch-aaa-dual/assets").glob(
                    "*.schema.json"
                )
            )
            self.assertEqual(
                ["arch-aaa-dual.schema.json", "architecture.schema.json"],
                schema_names,
            )
            schema_path = evaluations._resolve_case_schema(
                {"skill": "arch-aaa-dual"}, "architecture", temp_root
            )
            self.assertEqual("architecture.schema.json", schema_path.name)

    def test_all_six_suite_semantic_validators_enforce_invariants_during_grading(
        self,
    ) -> None:
        semantic_failures: list[tuple[str, str, dict[str, object]]] = [
            (
                "spec-traversal-scope",
                "spec-authoring-b",
                {
                    "schema_version": "1.0",
                    "title": "Traversal Spec",
                    "status": "approved",
                    "summary": "Invalid traversal scope.",
                    "interview_decisions": [],
                    "requirements": [],
                    "new_file_scopes": ["../escape.py"],
                    "out_of_scope": [],
                    "verification_commands": ["python -m unittest"],
                    "convergence_checks": [],
                },
            ),
            (
                "spec-contradicted-converged",
                "spec-convergence-b",
                {
                    "schema_version": "1.0",
                    "title": "Contradicted Convergence",
                    "status": "converged",
                    "summary": "Claims converged despite out-of-scope check.",
                    "interview_decisions": [],
                    "requirements": [],
                    "new_file_scopes": ["invoice_service.py"],
                    "out_of_scope": [],
                    "verification_commands": ["python -m unittest"],
                    "convergence_checks": [
                        {
                            "id": "CHK-1",
                            "target": "audit_sink.py",
                            "status": "out-of-scope",
                            "evidence": "Drifted file.",
                        }
                    ],
                },
            ),
            (
                "workflow-commit-prefix-mismatch",
                "terse-output-commit-a",
                {
                    "schema_version": "1.0",
                    "compression_level": "standard",
                    "terse_response": "Fixed expiry comparison.",
                    "preserved_exact_spans": ["session_token.py"],
                    "commit_type": "fix",
                    "commit_subject": "feat: mismatched commit subject prefix",
                    "commit_body_bullets": ["Explain mismatch."],
                    "git_mutated": False,
                },
            ),
            (
                "workflow-plan-cycle",
                "repo-plan-fix-a",
                {
                    "task_breakdowns": [
                        {
                            "task_id": "T-1",
                            "title": "First",
                            "file_scopes": ["token_store.py"],
                            "blocked_by": ["T-2"],
                            "acceptance_criteria": ["Check first"],
                        },
                        {
                            "task_id": "T-2",
                            "title": "Second",
                            "file_scopes": ["session_gate.py"],
                            "blocked_by": ["T-1"],
                            "acceptance_criteria": ["Check second"],
                        },
                    ],
                    "dependency_order": ["T-1", "T-2"],
                    "file_scopes": ["token_store.py", "session_gate.py"],
                    "acceptance_criteria": ["All pass"],
                },
            ),
            (
                "review-approve-with-critical",
                "review-diff-analysis-b",
                {
                    "schema_version": "1.0",
                    "verdict": "approve",
                    "summary": "Approved despite critical issue.",
                    "findings": [
                        {
                            "id": "F-1",
                            "title": "Authentication bypass",
                            "severity": "critical",
                            "file": "auth.py",
                            "line": 10,
                            "description": "Empty bearer header returns admin role.",
                            "recommendation": "Reject missing bearer header.",
                        }
                    ],
                },
            ),
            (
                "architecture-traversal-boundary",
                "arch-boundary-specification-b",
                {
                    "schema_version": "1.0",
                    "title": "Invalid Boundary Path",
                    "status": "accepted",
                    "boundaries": [
                        {
                            "name": "escape-boundary",
                            "type": "module",
                            "description": "Invalid parent traversal path.",
                            "invariants": ["No direct DB writes."],
                            "paths": ["../outside.py"],
                        }
                    ],
                    "decisions": [],
                },
            ),
            (
                "testing-duplicate-test-ids",
                "test-gap-analysis-b",
                {
                    "schema_version": "1.0",
                    "component": "billing",
                    "framework": "unittest",
                    "test_cases": [
                        {
                            "id": "TC-DUP",
                            "name": "test_one",
                            "description": "first",
                            "oracle_type": "boundary",
                            "verified": True,
                        },
                        {
                            "id": "TC-DUP",
                            "name": "test_two",
                            "description": "second",
                            "oracle_type": "boundary",
                            "verified": True,
                        },
                    ],
                },
            ),
            (
                "audit-dangling-evidence-ref",
                "concurrency-b",
                self._make_audit_ledger(
                    findings=[self._make_audit_finding("F-1")],
                    unknown_evidence_ref=True,
                ),
            ),
        ]
        for label, case_id, payload in semantic_failures:
            with self.subTest(validator=label), tempfile.TemporaryDirectory() as temp:
                script = f"import json; open('result.json', 'w', encoding='utf-8').write(json.dumps({payload!r}))"
                res = evaluations.evaluate(
                    Path(temp),
                    [sys.executable, "-c", script],
                    cases=[case_id],
                )
                self.assertTrue(
                    all(
                        row["state"] == "failed" and not row["exact_match"]
                        for row in res["runs"]
                    ),
                    f"Expected semantic validator failure for {label}: {res['runs']}",
                )

    def test_max_tokens_budget_enforced_in_library_and_cli(self):
        empty_ledger = self._make_audit_ledger(findings=[])
        adapter_script = (
            "import json\n"
            f"payload = {{'deliverable': {empty_ledger!r}, 'usage': {{'tokens': 500}}}}\n"
            "open('result.json', 'w', encoding='utf-8').write(json.dumps(payload))\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            ok_res = evaluations.evaluate(
                Path(temp) / "ok",
                [sys.executable, "-c", adapter_script],
                cases=["concurrency-b"],
                max_tokens=500,
            )
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"]
                    for row in ok_res["runs"]
                )
            )

            over_res = evaluations.evaluate(
                Path(temp) / "over",
                [sys.executable, "-c", adapter_script],
                cases=["concurrency-b"],
                max_tokens=499,
            )
            self.assertTrue(
                all(
                    row["state"] == "failed"
                    and not row["exact_match"]
                    and "exceeded max_tokens" in str(row.get("error", ""))
                    for row in over_res["runs"]
                )
            )

            with self.assertRaises(ValueError):
                evaluations.evaluate(
                    Path(temp) / "invalid",
                    [sys.executable, "-c", adapter_script],
                    cases=["concurrency-b"],
                    max_tokens=0,
                )

            cli_out = Path(temp) / "cli_over"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/fred.py"),
                    "evaluate",
                    "--output",
                    str(cli_out),
                    "--adapter-argv",
                    json.dumps([sys.executable, "-c", adapter_script]),
                    "--case",
                    "concurrency-b",
                    "--max-tokens",
                    "499",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            bench = json.loads((cli_out / "benchmark.json").read_text(encoding="utf-8"))
            self.assertTrue(
                all(
                    row["state"] == "failed"
                    and not row["exact_match"]
                    and "exceeded max_tokens" in str(row.get("error", ""))
                    for row in bench["runs"]
                )
            )


if __name__ == "__main__":
    unittest.main()
