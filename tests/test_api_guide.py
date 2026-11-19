from __future__ import annotations

import importlib
import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True


class ApiGuideTests(unittest.TestCase):
    def setUp(self):
        self.api_doc_path = ROOT / "docs/api.md"
        self.assertTrue(self.api_doc_path.is_file(), "docs/api.md must exist")
        self.api_text = self.api_doc_path.read_text(encoding="utf-8")

    def test_fredlib_init_exports_and_docstring(self):
        import fredlib

        self.assertTrue(fredlib.__doc__, "fredlib must have a package docstring")
        self.assertTrue(hasattr(fredlib, "__all__"), "fredlib must declare __all__")
        expected_modules = {
            "evaluations",
            "installation",
            "releases",
            "reports",
            "repository",
            "runs",
            "vetting",
        }
        self.assertTrue(expected_modules.issubset(set(fredlib.__all__)))
        for mod_name in expected_modules:
            self.assertTrue(hasattr(fredlib, mod_name))

    def test_all_seven_modules_have_sections(self):
        modules = [
            "fredlib.runs",
            "fredlib.installation",
            "fredlib.evaluations",
            "fredlib.reports",
            "fredlib.releases",
            "fredlib.repository",
            "fredlib.vetting",
        ]
        for mod in modules:
            self.assertIn(
                f"## Module: `{mod}`",
                self.api_text,
                f"Missing section for module {mod}",
            )

    def test_all_public_functions_and_parameters_documented(self):
        modules = [
            "runs",
            "installation",
            "evaluations",
            "reports",
            "releases",
            "repository",
            "vetting",
        ]
        total_functions = 0
        for mod_name in modules:
            mod = importlib.import_module(f"fredlib.{mod_name}")
            public_funcs = [
                (n, o)
                for n, o in inspect.getmembers(mod, inspect.isfunction)
                if not n.startswith("_") and o.__module__ == mod.__name__
            ]
            for fname, func in public_funcs:
                total_functions += 1
                heading = f"### `{fname}`"
                self.assertIn(
                    heading,
                    self.api_text,
                    f"Function {mod_name}.{fname} must have heading {heading}",
                )
                sig = inspect.signature(func)
                for param in sig.parameters.values():
                    # Variable positional or keyword arguments may be documented with prefix
                    valid_markers = [
                        f"`{param.name}`",
                        f"`*{param.name}`",
                        f"`**{param.name}`",
                    ]
                    self.assertTrue(
                        any(m in self.api_text for m in valid_markers),
                        f"Parameter {param.name} of {mod_name}.{fname} must be documented",
                    )
        self.assertEqual(total_functions, 41, "Expected exactly 41 public functions")

    def test_private_helpers_are_not_publicly_documented(self):
        self.assertNotIn("### `_receipt_identifies_host`", self.api_text)
        self.assertNotIn("### `_parse_sarif_uri`", self.api_text)
        self.assertIn(
            "def import_sarif(path: Path, root: Path | None = None) -> dict",
            self.api_text,
        )

    def test_cross_references_in_guides(self):
        toolkit_path = ROOT / "docs/toolkit.md"
        architecture_path = ROOT / "docs/architecture.md"
        self.assertTrue(toolkit_path.is_file())
        self.assertTrue(architecture_path.is_file())

        toolkit_text = toolkit_path.read_text(encoding="utf-8")
        architecture_text = architecture_path.read_text(encoding="utf-8")

        self.assertIn("api.md", toolkit_text, "toolkit.md must link to api.md")
        self.assertIn(
            "api.md", architecture_text, "architecture.md must link to api.md"
        )

        # Verify relative links from toolkit and architecture resolve
        for source_path, text in [
            (toolkit_path, toolkit_text),
            (architecture_path, architecture_text),
            (self.api_doc_path, self.api_text),
        ]:
            for target in re.findall(r"\[.+?\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                file_target = target.split("#")[0]
                if file_target:
                    resolved = (source_path.parent / file_target).resolve()
                    self.assertTrue(
                        resolved.is_file(),
                        f"Broken link in {source_path.name}: {target} -> {resolved}",
                    )

    def test_api_guide_internal_anchors(self):
        headings = re.findall(r"^#{1,6}\s+(.+)$", self.api_text, re.MULTILINE)

        def slugify(text: str) -> str:
            text = text.lower()
            text = re.sub(r"[`]", "", text)
            text = re.sub(r"[^a-z0-9 _-]", "", text)
            return text.strip().replace(" ", "-")

        anchors = {slugify(h) for h in headings}
        toc_links = re.findall(r"\[.+?\]\(#([a-z0-9_-]+)\)", self.api_text)
        self.assertTrue(len(toc_links) > 0, "TOC links must exist")
        for anchor in toc_links:
            self.assertIn(
                anchor,
                anchors,
                f"TOC anchor #{anchor} does not match any heading in docs/api.md",
            )


if __name__ == "__main__":
    unittest.main()
