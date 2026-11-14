import sys
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

    def test_searchable_experience_is_current(self):
        self.assertEqual([], build(check=True))

    def test_no_host_adapter_claims_behavioral_certification(self):
        for host in read_json(ROOT / "catalog/hosts.json")["hosts"].values():
            self.assertIn("not-executed", host["behavior_test"])


if __name__ == "__main__":
    unittest.main()
