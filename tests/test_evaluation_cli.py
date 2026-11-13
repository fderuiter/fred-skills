import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from fredlib import evaluations


class EvaluationTests(unittest.TestCase):
    def test_reference_adapter_exercises_all_cases_without_claiming_model_quality(self):
        argv = [sys.executable, str(ROOT / "evals/reference_adapter.py"), "--prompt", "{prompt_file}", "--output", "{output_file}"]
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(Path(temp), argv)
            self.assertEqual(30, len(result["runs"]))
            self.assertTrue(all(row["exact_match"] for row in result["runs"]))
            self.assertTrue(all(row["actor"]["kind"] == "deterministic-fixture-adapter" for row in result["runs"]))
            request = (Path(temp) / "concurrency-a/with_skill/1/prompt.md").read_text()
            self.assertNotIn("expected_keys", request)
            self.assertFalse((Path(temp) / "concurrency-a/without_skill/1/skill").exists())
            self.assertTrue((Path(temp) / "concurrency-a/with_skill/1/skill/SKILL.md").exists())

    def test_missing_adapter_result_is_a_failed_run(self):
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(Path(temp), [sys.executable, "-c", "pass"], cases=["concurrency-b"])
            self.assertTrue(all(row["state"] == "failed" and not row["exact_match"] for row in result["runs"]))

    def test_timeout_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(Path(temp), [sys.executable, "-c", "import time; time.sleep(2)"], cases=["concurrency-b"], timeout=1)
            self.assertTrue(all(row["state"] == "timeout" for row in result["runs"]))

    def test_malformed_usage_does_not_abort_and_missing_findings_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = [sys.executable, "-c", "import json; json.dump({'findings': [], 'usage': None}, open('result.json', 'w'))"]
            result = evaluations.evaluate(Path(temp), adapter, cases=["concurrency-routing"])
            self.assertTrue(all(row["tokens"] is None for row in result["runs"]))
            self.assertTrue(all(row["state"] == "executed" for row in result["runs"]))
        with tempfile.TemporaryDirectory() as temp:
            result = evaluations.evaluate(Path(temp), [sys.executable, "-c", "open('result.json', 'w').write('{}')"], cases=["concurrency-routing"])
            self.assertTrue(all(row["state"] == "failed" for row in result["runs"]))

    def test_source_mutation_invalidates_evaluation(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = [sys.executable, "-c", "from pathlib import Path; Path('source/injected').write_text('changed'); Path('result.json').write_text('{\"findings\": []}')"]
            result = evaluations.evaluate(Path(temp), adapter, cases=["concurrency-routing"])
            self.assertTrue(all(row["inputs_changed"] and row["state"] == "failed" for row in result["runs"]))

    def test_cli_discovery_and_error_paths(self):
        for args in [["packs"], ["tasks"], ["hosts"], ["search", "stale response"]]:
            proc = subprocess.run([sys.executable, str(ROOT / "scripts/fred.py"), *args], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(json.loads(proc.stdout))
        bad = subprocess.run([sys.executable, str(ROOT / "scripts/fred.py"), "evaluate", "--output", "unused", "--adapter-argv", "not-json"], capture_output=True, text=True)
        self.assertEqual(2, bad.returncode)


if __name__ == "__main__":
    unittest.main()
