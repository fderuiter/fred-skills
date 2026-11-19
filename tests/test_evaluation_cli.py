from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import evaluations
from validate_skills import validate_skill


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
        manifest = json.loads((ROOT / "evals/cases.json").read_text(encoding="utf-8"))
        self.assertEqual(60, len(manifest["cases"]))
        skills_covered = {case["skill"] for case in manifest["cases"]}
        self.assertEqual(20, len(skills_covered))

        suites_doc = json.loads(
            (ROOT / "catalog/suites.json").read_text(encoding="utf-8")
        )
        skill_to_suite = {
            skill: suite["name"]
            for suite in suites_doc["suites"]
            for phase in suite["phases"]
            for skill in phase["skills"]
        }
        suites_covered = {skill_to_suite[skill] for skill in skills_covered}
        self.assertEqual(
            {
                "repository-audits",
                "repository-workflows",
                "software-architecture",
                "code-review",
                "automated-testing",
            },
            suites_covered,
        )
        non_audit_skills = {
            s for s, st in skill_to_suite.items() if st != "repository-audits"
        }
        self.assertEqual(15, len(non_audit_skills))
        self.assertTrue(non_audit_skills.issubset(skills_covered))

        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            result = evaluations.evaluate(temp_path, argv)
            self.assertEqual(120, len(result["runs"]))
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"]
                    for row in result["runs"]
                )
            )
            self.assertTrue(
                all(
                    row["actor"]["kind"] == "deterministic-fixture-adapter"
                    for row in result["runs"]
                )
            )

            # Verify all 15 non-audit skills recorded state=="executed" and exact_match==True
            case_to_skill = {c["id"]: c["skill"] for c in manifest["cases"]}
            non_audit_runs = [
                row
                for row in result["runs"]
                if case_to_skill[row["case"]] in non_audit_skills
            ]
            self.assertEqual(90, len(non_audit_runs))
            self.assertTrue(
                all(
                    row["state"] == "executed" and row["exact_match"] is True
                    for row in non_audit_runs
                )
            )

            # Verify prompts never leak grading expectations or writable_paths rules
            for case in manifest["cases"]:
                for cfg in ("without_skill", "with_skill"):
                    prompt_text = (
                        temp_path / case["id"] / cfg / "1/prompt.md"
                    ).read_text(encoding="utf-8")
                    self.assertNotIn("expected_keys", prompt_text)
                    self.assertNotIn("expected_assertions", prompt_text)
                    self.assertNotIn("writable_paths", prompt_text)

            self.assertFalse(
                (temp_path / "concurrency-a/without_skill/1/skill").exists()
            )
            self.assertTrue(
                (temp_path / "concurrency-a/with_skill/1/skill/SKILL.md").exists()
            )

            # Reference adapter omits usage -> token aggregates preserve None
            for cfg in ("without_skill", "with_skill"):
                self.assertEqual(0, result["summary"][cfg]["tokens_reported_runs"])
                self.assertIsNone(result["summary"][cfg]["total_tokens"])
                self.assertIsNone(result["summary"][cfg]["mean_tokens"])
            lift = result["summary"]["skill_lift"]
            self.assertEqual(60, lift["paired_runs"])
            self.assertEqual(0.0, lift["exact_match_rate_delta"])
            self.assertEqual(0, lift["paired_token_runs"])
            self.assertIsNone(lift["total_tokens_delta"])
            self.assertIsNone(lift["mean_tokens_delta"])

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

    def test_summary_reports_tokens_and_paired_skill_lift(self):
        lift_script = (
            "import json\n"
            "from pathlib import Path\n"
            "has_skill = Path('skill/SKILL.md').is_file()\n"
            "findings = [{'key': 'stale-publication', 'status': 'supported', 'evidence': 'session.mjs:8'}] if has_skill else []\n"
            "tokens = 420 if has_skill else 300\n"
            "Path('result.json').write_text(json.dumps({'findings': findings, 'usage': {'tokens': tokens}}), encoding='utf-8')\n"
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
        with tempfile.TemporaryDirectory() as temp:
            adapter = [
                sys.executable,
                "-c",
                "import json; json.dump({'findings': [], 'usage': None}, open('result.json', 'w'))",
            ]
            result = evaluations.evaluate(
                Path(temp), adapter, cases=["concurrency-routing"]
            )
            self.assertTrue(all(row["tokens"] is None for row in result["runs"]))
            self.assertTrue(all(row["state"] == "executed" for row in result["runs"]))
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


if __name__ == "__main__":
    unittest.main()
