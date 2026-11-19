"""Unit and CLI tests for all five standalone suite artifact validators."""

from __future__ import annotations

import builtins
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_architecture
import validate_audit
import validate_review
import validate_skills
import validate_testing
import validate_workflow


class SuiteValidatorsTests(unittest.TestCase):
    """Verify schema and cross-record checks across all 5 suite validators."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run_cli(
        self, script_name: str, payload: Any, schema_path: Path
    ) -> subprocess.CompletedProcess[str]:
        artifact = self.tmp_dir / "artifact.json"
        if isinstance(payload, str):
            artifact.write_text(payload, encoding="utf-8")
        else:
            artifact.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / script_name),
                str(artifact),
                "--schema",
                str(schema_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_all_51_skill_worked_example_json_blocks_pass_suite_semantic_validators(
        self,
    ) -> None:
        suites = json.loads((ROOT / "catalog/suites.json").read_text(encoding="utf-8"))[
            "suites"
        ]
        skill_to_suite = {
            sk: s["name"]
            for s in suites
            for phase in s["phases"]
            for sk in phase["skills"]
        }
        suite_validators = {
            "repository-audits": validate_audit,
            "repository-workflows": validate_workflow,
            "software-architecture": validate_architecture,
            "code-review": validate_review,
            "automated-testing": validate_testing,
        }

        for skill_dir in sorted((ROOT / "skills").iterdir()):
            if not skill_dir.is_dir():
                continue
            suite_name = skill_to_suite[skill_dir.name]
            validator_mod = suite_validators[suite_name]
            schemas = sorted((skill_dir / "assets").glob("*.schema.json"))
            found_json = 0
            for ref_name in ("method.md", "examples.md"):
                ref_path = skill_dir / "references" / ref_name
                if not ref_path.is_file():
                    continue
                blocks, unclosed = validate_skills._extract_fenced_blocks(
                    ref_path.read_text(encoding="utf-8")
                )
                self.assertFalse(unclosed, f"Unclosed code fence in {ref_path}")
                for lang, body in blocks:
                    if lang != "json":
                        continue
                    found_json += 1
                    data = json.loads(body)
                    matched = False
                    for sp in schemas:
                        schema_obj = json.loads(sp.read_text(encoding="utf-8"))
                        if sp.name == "bundle.schema.json":
                            import jsonschema

                            errs = [
                                e.message
                                for e in jsonschema.Draft202012Validator(
                                    schema_obj
                                ).iter_errors(data)
                            ]
                        else:
                            errs = validator_mod.validate(data, schema_obj)
                        if not errs:
                            matched = True
                            break
                    self.assertTrue(
                        matched,
                        f"{skill_dir.name}/{ref_name} JSON block failed suite validator",
                    )
            self.assertGreaterEqual(found_json, 1, skill_dir.name)

    def test_validate_workflow_enforces_paths_cycles_and_status_consistency(
        self,
    ) -> None:
        plan_schema_path = ROOT / "skills/repo-plan-fix/assets/plan.schema.json"
        plan_schema = json.loads(plan_schema_path.read_text(encoding="utf-8"))
        valid_plan: dict[str, Any] = {
            "schema_version": "1.0",
            "plan_id": "PLAN-100",
            "title": "Add tenant filter guard",
            "file_scopes": ["src/invoices/query.py"],
            "acceptance_criteria": ["Cross-tenant invoice fetch returns 404"],
            "task_breakdowns": [
                {
                    "task_id": "T-1",
                    "title": "Bind tenant_id in query",
                    "file_scopes": ["src/invoices/query.py"],
                    "acceptance_criteria": ["Query binds tenant_id"],
                    "blocked_by": [],
                },
                {
                    "task_id": "T-2",
                    "title": "Add regression test",
                    "file_scopes": ["tests/test_invoices.py"],
                    "acceptance_criteria": ["Regression test passes"],
                    "blocked_by": ["T-1"],
                },
            ],
            "dependency_order": ["T-1", "T-2"],
        }
        self.assertEqual([], validate_workflow.validate(valid_plan, plan_schema))
        ok_proc = self._run_cli("validate_workflow.py", valid_plan, plan_schema_path)
        self.assertEqual(0, ok_proc.returncode, ok_proc.stderr)
        self.assertIn("PASS:", ok_proc.stdout)

        bad_plan: dict[str, Any] = {
            **valid_plan,
            "file_scopes": ["/etc/passwd"],
            "task_breakdowns": [
                {
                    "task_id": "T-1",
                    "title": "A",
                    "file_scopes": ["src/a.py"],
                    "acceptance_criteria": ["A"],
                    "blocked_by": ["T-2"],
                },
                {
                    "task_id": "T-2",
                    "title": "B",
                    "file_scopes": ["src/b.py"],
                    "acceptance_criteria": ["B"],
                    "blocked_by": ["T-1"],
                },
            ],
            "dependency_order": ["T-1", "T-999"],
        }
        errs = validate_workflow.validate(bad_plan, plan_schema)
        self.assertTrue(any("repository-relative" in e for e in errs), errs)
        self.assertTrue(any("Task dependency cycle" in e for e in errs), errs)
        self.assertTrue(any("T-999" in e for e in errs), errs)
        fail_proc = self._run_cli("validate_workflow.py", bad_plan, plan_schema_path)
        self.assertEqual(1, fail_proc.returncode)

        finding_schema = json.loads(
            (
                ROOT
                / "skills/repo-verify-finding/assets/finding-verification.schema.json"
            ).read_text(encoding="utf-8")
        )
        contradictory_finding: dict[str, Any] = {
            "finding_id": "F-100",
            "reproduction_status": "confirmed",
            "safe_control_outcomes": [{"control": "tenant match", "passed": True}],
            "confidence_rating": "high",
            "reproduced": False,
            "commands": [],
        }
        f_errs = validate_workflow.validate(contradictory_finding, finding_schema)
        self.assertTrue(any("reproduced to false" in e for e in f_errs), f_errs)
        self.assertTrue(any("non-empty commands" in e for e in f_errs), f_errs)

    def test_validate_architecture_enforces_unique_boundaries_and_decisions(
        self,
    ) -> None:
        schema_path = (
            ROOT
            / "shared/software-architecture/resources/assets/architecture.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        valid = {
            "schema_version": "1.0",
            "title": "Billing Service Boundaries",
            "status": "accepted",
            "boundaries": [
                {"name": "public-api", "type": "trust", "invariants": ["Verify JWT"]}
            ],
            "decisions": [
                {
                    "id": "ADR-001",
                    "title": "Use transactional outbox",
                    "context": "Dual write risk",
                    "decision": "Write outbox in SQL tx",
                    "consequences": ["At-least-once delivery"],
                }
            ],
        }
        self.assertEqual([], validate_architecture.validate(valid, schema))
        self.assertEqual(
            0, self._run_cli("validate_architecture.py", valid, schema_path).returncode
        )

        dup = {
            **valid,
            "boundaries": [valid["boundaries"][0], valid["boundaries"][0]],
            "decisions": [valid["decisions"][0], valid["decisions"][0]],
        }
        errs = validate_architecture.validate(dup, schema)
        self.assertTrue(any("Duplicate names in boundaries" in e for e in errs), errs)
        self.assertTrue(any("Duplicate IDs in decisions" in e for e in errs), errs)
        self.assertEqual(
            1, self._run_cli("validate_architecture.py", dup, schema_path).returncode
        )

    def test_validate_review_enforces_relative_paths_and_verdict_alignment(
        self,
    ) -> None:
        schema_path = ROOT / "shared/code-review/resources/assets/review.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        finding: dict[str, Any] = {
            "id": "REV-1",
            "title": "Missing tenant scope check",
            "severity": "high",
            "file": "src/auth/guard.py",
            "line": 18,
            "description": "Query omits session tenant_id filter.",
            "recommendation": "Bind tenant_id from verified session.",
        }
        valid: dict[str, Any] = {
            "schema_version": "1.0",
            "verdict": "request_changes",
            "summary": "High-severity tenant scope bypass in guard.py.",
            "findings": [finding],
        }
        self.assertEqual([], validate_review.validate(valid, schema))
        self.assertEqual(
            0, self._run_cli("validate_review.py", valid, schema_path).returncode
        )

        contradictory: dict[str, Any] = {
            **valid,
            "verdict": "approve",
            "findings": [
                {**finding, "file": "../outside/guard.py"},
                dict(finding),
            ],
        }
        errs = validate_review.validate(contradictory, schema)
        self.assertTrue(any("Duplicate IDs in findings" in e for e in errs), errs)
        self.assertTrue(any("repository-relative" in e for e in errs), errs)
        self.assertTrue(any("Verdict 'approve' conflicts" in e for e in errs), errs)
        self.assertEqual(
            1,
            self._run_cli("validate_review.py", contradictory, schema_path).returncode,
        )

    def test_validate_testing_enforces_unique_ids_and_relative_component_paths(
        self,
    ) -> None:
        schema_path = (
            ROOT / "shared/automated-testing/resources/assets/testing.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        tc: dict[str, Any] = {
            "id": "TC-001",
            "name": "test_reject_when_bucket_exhausted",
            "description": "Verify consume(1) returns False when tokens == 0.",
            "oracle_type": "boundary",
            "verified": True,
        }
        valid: dict[str, Any] = {
            "schema_version": "1.0",
            "component": "src/ratelimit/bucket.py",
            "framework": "pytest",
            "test_cases": [tc],
        }
        self.assertEqual([], validate_testing.validate(valid, schema))
        self.assertEqual(
            0, self._run_cli("validate_testing.py", valid, schema_path).returncode
        )

        invalid: dict[str, Any] = {
            **valid,
            "component": "/abs/path/bucket.py",
            "test_cases": [tc, dict(tc)],
        }
        errs = validate_testing.validate(invalid, schema)
        self.assertTrue(any("repository-relative" in e for e in errs), errs)
        self.assertTrue(any("Duplicate IDs in test_cases" in e for e in errs), errs)
        self.assertEqual(
            1, self._run_cli("validate_testing.py", invalid, schema_path).returncode
        )

    def test_malformed_json_and_missing_jsonschema_exit_with_code_2(self) -> None:
        schema_path = ROOT / "shared/code-review/resources/assets/review.schema.json"
        for script_name in (
            "validate_audit.py",
            "validate_workflow.py",
            "validate_architecture.py",
            "validate_review.py",
            "validate_testing.py",
        ):
            bad_proc = self._run_cli(script_name, "{not-valid-json", schema_path)
            self.assertEqual(2, bad_proc.returncode, script_name)

        real_import = builtins.__import__

        def block_jsonschema(name: str, *args: object, **kwargs: object) -> Any:
            if name == "jsonschema":
                raise ImportError("No module named 'jsonschema'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        for mod in (
            validate_audit,
            validate_workflow,
            validate_architecture,
            validate_review,
            validate_testing,
        ):
            with (
                patch.object(builtins, "__import__", side_effect=block_jsonschema),
                self.assertRaises(RuntimeError),
            ):
                mod.validate({}, {})


if __name__ == "__main__":
    unittest.main()
