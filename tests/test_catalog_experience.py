import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_experience import build
from fredlib.common import read_json


class ExperienceTests(unittest.TestCase):
    def test_every_pack_recipe_and_profile_resolves_real_skills(self):
        known = {
            item["name"] for item in read_json(ROOT / "catalog/skills.json")["skills"]
        }
        for filename, key, field in [
            ("packs", "packs", "skills"),
            ("recipes", "recipes", "skills"),
            ("profiles", "profiles", "priorities"),
        ]:
            for name, item in read_json(ROOT / "catalog" / (filename + ".json"))[
                key
            ].items():
                with self.subTest(name=name):
                    self.assertTrue(item[field])
                    self.assertFalse(set(item[field]) - known)

        marketplace_plugins = {
            plugin["name"]: plugin
            for plugin in read_json(ROOT / ".claude-plugin/marketplace.json")["plugins"]
        }
        for skill_name in known:
            self.assertIn(skill_name, marketplace_plugins)
        for pack_name, pack_data in read_json(ROOT / "catalog/packs.json")[
            "packs"
        ].items():
            self.assertIn(pack_name, marketplace_plugins)
            bundle = marketplace_plugins[pack_name]
            self.assertEqual(bundle["source"], "./")
            self.assertIs(bundle["strict"], False)
            self.assertEqual(
                bundle["skills"],
                [f"./skills/{member}" for member in pack_data["skills"]],
            )

    def test_searchable_experience_is_current(self):
        self.assertEqual([], build(check=True))
        page = (ROOT / "docs/explorer.html").read_text(encoding="utf-8")
        self.assertIn('<div id="empty-state" class="empty-state" hidden>', page)
        self.assertIn('id="reset-filters"', page)
        self.assertIn(
            '<a href="#repo-audit-concurrency">repo-audit-concurrency</a>', page
        )
        self.assertIn('<article class="finding" id="repo-audit-concurrency"', page)
        recipes = read_json(ROOT / "catalog/recipes.json")["recipes"]
        for item in recipes.values():
            for skill in item["skills"]:
                self.assertIn(f'<a href="#{skill}">{skill}</a>', page)
                self.assertIn(f'id="{skill}"', page)
        suites = read_json(ROOT / "catalog/suites.json")["suites"]
        select_start = page.find('<select id="status">')
        select_end = page.find("</select>", select_start)
        select_html = page[select_start:select_end]
        for suite in suites:
            self.assertIn(f'<option value="{suite["name"]}">', select_html)

    def test_experience_detects_drift(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            catalog_dir = tmp_root / "catalog"
            catalog_dir.mkdir(parents=True)
            for file in ["skills.json", "recipes.json", "packs.json", "hosts.json"]:
                (catalog_dir / file).write_bytes((ROOT / "catalog" / file).read_bytes())
            self.assertEqual([], build(root=tmp_root))
            self.assertEqual([], build(root=tmp_root, check=True))
            (tmp_root / "docs/experience.md").write_text("drift", encoding="utf-8")
            errors = build(root=tmp_root, check=True)
            self.assertTrue(any("docs/experience.md" in err for err in errors))

    def test_no_host_adapter_claims_behavioral_certification(self):
        for host in read_json(ROOT / "catalog/hosts.json")["hosts"].values():
            self.assertIn("not-executed", host["behavior_test"])

    def test_domain_specific_task_recipes(self):
        recipes = read_json(ROOT / "catalog/recipes.json")["recipes"]
        experience_md = (ROOT / "docs/experience.md").read_text(encoding="utf-8")
        explorer_html = (ROOT / "docs/explorer.html").read_text(encoding="utf-8")
        expected_recipes = {
            "design-architecture": {
                "skills": [
                    "arch-decision-record",
                    "arch-boundary-specification",
                    "arch-component-decomposition",
                ],
                "requires_base": False,
                "deliverable": "architecture.json",
            },
            "review-code-changes": {
                "skills": [
                    "review-diff-analysis",
                    "review-invariant-violations",
                    "review-actionable-feedback",
                ],
                "requires_base": True,
                "deliverable": "review.json",
            },
            "expand-automated-tests": {
                "skills": [
                    "test-gap-analysis",
                    "test-case-generation",
                    "test-suite-verification",
                ],
                "requires_base": False,
                "deliverable": "testing.json",
            },
        }
        for name, expected in expected_recipes.items():
            self.assertIn(name, recipes)
            self.assertEqual(expected["skills"], recipes[name]["skills"])
            self.assertEqual(expected["requires_base"], recipes[name]["requires_base"])
            self.assertTrue(recipes[name]["outcome"])
            self.assertTrue(recipes[name]["workflows"])
            self.assertTrue(recipes[name]["outputs"])
            self.assertEqual(expected["deliverable"], recipes[name]["outputs"][0])
            self.assertNotIn("audit.json", recipes[name]["outputs"])
            self.assertIn(expected["deliverable"], experience_md)
            self.assertIn(expected["deliverable"], explorer_html)

    def test_suite_artifact_contracts_and_readme_quickstart(self):
        import jsonschema

        suites = {
            item["name"]: item
            for item in read_json(ROOT / "catalog/suites.json")["suites"]
        }
        expected_artifacts = {
            "repository-audits": (
                "audit.json",
                "shared/repo-audit/resources/assets/audit.schema.json",
            ),
            "repository-workflows": (
                "audit.json",
                "shared/repo-audit/resources/assets/audit.schema.json",
            ),
            "software-architecture": (
                "architecture.json",
                "shared/software-architecture/resources/assets/architecture.schema.json",
            ),
            "code-review": (
                "review.json",
                "shared/code-review/resources/assets/review.schema.json",
            ),
            "automated-testing": (
                "testing.json",
                "shared/automated-testing/resources/assets/testing.schema.json",
            ),
        }
        self.assertEqual(set(expected_artifacts), set(suites))
        for suite_name, (filename, schema_rel) in expected_artifacts.items():
            with self.subTest(suite=suite_name):
                artifact = suites[suite_name].get("artifact")
                self.assertIsInstance(artifact, dict)
                self.assertEqual(filename, artifact["filename"])
                self.assertEqual(schema_rel, artifact["schema"])
                schema_path = ROOT / schema_rel
                self.assertTrue(schema_path.is_file())
                starter = artifact["starter"]
                self.assertIsInstance(starter, dict)
                self.assertTrue(starter)
                if filename != "audit.json":
                    schema = read_json(schema_path)
                    errors = list(
                        jsonschema.Draft202012Validator(schema).iter_errors(starter)
                    )
                    self.assertEqual([], errors, [e.message for e in errors])

        readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        quickstart_header = "\n".join(readme_lines[:28])
        for recipe_name in (
            "design-architecture",
            "review-code-changes",
            "expand-automated-tests",
        ):
            self.assertIn(f"`{recipe_name}`", quickstart_header)

    def test_workflow_recipes_companions_prompts_and_status_alignment(self):
        suites = {
            item["name"]: item
            for item in read_json(ROOT / "catalog/suites.json")["suites"]
        }
        expected_companions = {
            "finding-verification.json": "skills/repo-verify-finding/assets/finding-verification.schema.json",
            "regression.json": "skills/repo-specify-regression/assets/regression.schema.json",
            "plan.json": "skills/repo-plan-fix/assets/plan.schema.json",
            "fix.json": "skills/repo-apply-fix/assets/fix.schema.json",
            "verification.json": "skills/repo-verify-fix/assets/verification.schema.json",
            "reconciliation.json": "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
            "remediation.json": "skills/repo-reconcile-findings/assets/reconciliation.schema.json",
        }
        companions = suites["repository-workflows"].get("companions")
        self.assertEqual(expected_companions, companions)
        for schema_rel in expected_companions.values():
            self.assertTrue((ROOT / schema_rel).is_file(), schema_rel)

        recipes = read_json(ROOT / "catalog/recipes.json")["recipes"]
        experience_md = (ROOT / "docs/experience.md").read_text(encoding="utf-8")
        explorer_html = (ROOT / "docs/explorer.html").read_text(encoding="utf-8")
        expected_recipe_companions = {
            "investigate-bug": ["finding-verification.json"],
            "flaky-tests": ["finding-verification.json"],
            "plan-findings": ["finding-verification.json", "plan.json"],
            "verify-fix": [
                "finding-verification.json",
                "regression.json",
                "verification.json",
                "reconciliation.json",
                "remediation.json",
            ],
            "apply-fix": [
                "finding-verification.json",
                "regression.json",
                "plan.json",
                "fix.json",
                "verification.json",
                "reconciliation.json",
                "remediation.json",
            ],
        }
        for recipe_name, req_files in expected_recipe_companions.items():
            outputs = recipes[recipe_name]["outputs"]
            self.assertEqual("audit.json", outputs[0])
            for fname in req_files:
                self.assertIn(fname, outputs)
                self.assertIn(fname, experience_md)
                self.assertIn(fname, explorer_html)

        prompt_expectations = {
            "repo-verify-finding": (
                ["finding-verification.json"],
                "assets/finding-verification.schema.json",
            ),
            "repo-specify-regression": (
                ["regression.json"],
                "assets/regression.schema.json",
            ),
            "repo-plan-fix": (["plan.json"], "assets/plan.schema.json"),
            "repo-apply-fix": (["fix.json"], "assets/fix.schema.json"),
            "repo-verify-fix": (
                ["verification.json"],
                "assets/verification.schema.json",
            ),
            "repo-reconcile-findings": (
                ["reconciliation.json", "remediation.json"],
                "assets/reconciliation.schema.json",
            ),
        }
        for skill_name, (deliverables, schema_ref) in prompt_expectations.items():
            prompt_text = (ROOT / "skills" / skill_name / "assets/prompt.md").read_text(
                encoding="utf-8"
            )
            for deliverable in deliverables:
                self.assertIn(deliverable, prompt_text)
            self.assertIn(schema_ref, prompt_text)

        audit_statuses = set(
            read_json(ROOT / "shared/repo-audit/resources/assets/audit.schema.json")[
                "properties"
            ]["findings"]["items"]["properties"]["status"]["enum"]
        )
        fv_statuses = read_json(
            ROOT / "skills/repo-verify-finding/assets/finding-verification.schema.json"
        )["properties"]["reproduction_status"]["enum"]
        self.assertIn("supported", fv_statuses)
        self.assertNotIn("statically-supported", fv_statuses)
        self.assertIn("supported", audit_statuses)
        skill_md = (ROOT / "skills/repo-verify-finding/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("statically supported", skill_md)
        self.assertNotIn("statically-supported", skill_md)


if __name__ == "__main__":
    unittest.main()
