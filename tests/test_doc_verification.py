"""Tests for documentation verification harnesses: relative links and CLI snippet verification."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_doc_links import check_docs, check_file_links, extract_relative_links
from verify_doc_snippets import (
    DOCUMENTED_RANGES,
    discover_doc_blocks,
    execute_snippet_command,
    extract_blocks_from_file,
    get_registered_fred_subcommands,
    verify_all_snippets,
    verify_toolkit_subcommands,
)


class DocLinkVerificationTests(unittest.TestCase):
    """Test verification of relative links in repository documentation."""

    def test_repository_relative_links_resolve(self):
        """All relative file links across repository documentation files must resolve."""
        errors = check_docs(root=ROOT)
        self.assertEqual([], errors, f"Found broken relative links: {errors}")

    def test_extract_relative_links_filters_external_and_fragments(self):
        """External URLs and in-page anchor links must be ignored."""
        content = (
            "Check [Section](#heading) and [Site](https://example.com/page).\n"
            "Also [Local doc](docs/experience.md) and [Target](docs/toolkit.md#discover).\n"
        )
        links = extract_relative_links(content)
        self.assertIn("docs/experience.md", links)
        self.assertIn("docs/toolkit.md#discover", links)
        self.assertNotIn("#heading", links)
        self.assertNotIn("https://example.com/page", links)

    def test_missing_relative_link_is_detected(self):
        """Broken relative paths must be reported as errors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            doc = tmp_path / "test.md"
            doc.write_text("[Missing](does_not_exist.md)\n", encoding="utf-8")
            errors = check_file_links(doc, root=tmp_path)
            self.assertTrue(errors)
            self.assertIn("does_not_exist.md", errors[0])

    def test_existing_relative_link_passes(self):
        """Resolvable relative links must pass without errors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            target = tmp_path / "target.md"
            target.write_text("# Target\n", encoding="utf-8")
            doc = tmp_path / "test.md"
            doc.write_text("[Target](target.md)\n", encoding="utf-8")
            errors = check_file_links(doc, root=tmp_path)
            self.assertEqual([], errors)


class DocSnippetVerificationTests(unittest.TestCase):
    """Test discovery, extraction, and execution of documentation command snippets."""

    def test_discover_command_blocks_across_all_five_targets(self):
        """The test harness discovers command blocks in README, CONTRIBUTING, AGENTS, toolkit, and usage."""
        discovered = discover_doc_blocks(root=ROOT)
        self.assertEqual(
            set(DOCUMENTED_RANGES.keys()),
            set(discovered.keys()),
            "Must discover blocks in all 5 specified documentation files",
        )

        for rel_path, (s_line, e_line) in DOCUMENTED_RANGES.items():
            blocks = discovered[rel_path]
            self.assertTrue(
                len(blocks) > 0,
                f"Expected discovered blocks in {rel_path} (L{s_line}-L{e_line})",
            )
            for block in blocks:
                self.assertGreaterEqual(block.end_line, s_line)
                self.assertLessEqual(block.start_line, e_line)

    def test_toolkit_cli_subcommands_against_fred_lifecycle_groups(self):
        """Documented subcommands from docs/toolkit.md (L10-L171) must match scripts/fred.py:L1-L150."""
        errors = verify_toolkit_subcommands(root=ROOT)
        self.assertEqual(
            [], errors, f"Toolkit subcommand verification failed: {errors}"
        )

        # Explicitly verify key documented subcommands
        groups = get_registered_fred_subcommands(root=ROOT)
        registered = {cmd for cmds in groups.values() for cmd in cmds}

        expected_subcmds = [
            "tasks",
            "packs",
            "hosts",
            "search",
            "preflight",
            "plan",
            "install",
            "update",
            "remove",
            "rollback",
            "recover-install",
            "init",
            "checkpoint",
            "validate-run",
            "resume",
            "report",
            "issues",
            "compare",
            "memory",
            "import-sarif",
            "export-sarif",
            "reconcile",
            "release",
            "verify-release",
        ]
        for subcmd in expected_subcmds:
            self.assertIn(
                subcmd,
                registered,
                f"Expected subcommand '{subcmd}' missing from scripts/fred.py",
            )

    def test_extract_and_execute_snippets_from_readme_and_contributing(self):
        """The test suite must extract and execute shell command snippets from README.md and CONTRIBUTING.md."""
        errors = verify_all_snippets(root=ROOT)
        self.assertEqual(
            [], errors, f"Documentation snippet verification failed: {errors}"
        )

    def test_broken_command_detection(self):
        """Broken commands in snippets must be caught and reported."""
        # Non-existent script
        ok, msg = execute_snippet_command(
            "python scripts/nonexistent_script.py --check",
            root=ROOT,
        )
        self.assertFalse(ok)
        self.assertIn("does not exist", msg)

        # Invalid make target
        ok, msg = execute_snippet_command(
            "make non_existent_target",
            root=ROOT,
        )
        self.assertFalse(ok)

        # Invalid fred.py argument
        ok, msg = execute_snippet_command(
            "python scripts/fred.py search --invalid-flag-that-does-not-exist",
            root=ROOT,
        )
        self.assertFalse(ok)

    def test_line_range_filtering(self):
        """Blocks outside the specified range are ignored."""
        readme = ROOT / "README.md"
        # Only line 1 to 20 contains no code blocks
        blocks = extract_blocks_from_file(readme, start_line=1, end_line=20, root=ROOT)
        self.assertEqual([], blocks)

        # Lines 27 to 35 contains marketplace blocks
        blocks = extract_blocks_from_file(readme, start_line=27, end_line=35, root=ROOT)
        self.assertGreaterEqual(len(blocks), 1)

    def test_indented_code_blocks_in_runbooks(self):
        """Indented code blocks in docs/runbooks.md (L55-L57) must be discovered and validated."""
        runbooks = ROOT / "docs/runbooks.md"
        blocks = extract_blocks_from_file(
            runbooks, start_line=55, end_line=57, root=ROOT
        )
        self.assertGreaterEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.language, "sh")
        self.assertTrue(len(block.commands) > 0)
        line_no, cmd = block.commands[0]
        self.assertEqual(line_no, 56)
        self.assertIn("pgrep", cmd)
        ok, msg = execute_snippet_command(cmd, root=ROOT)
        self.assertTrue(ok, f"Runbooks snippet failed: {msg}")

    def test_multiline_commands_in_integration(self):
        """Multiline commands with continuation backslashes in docs/integration.md (L309-L340) must parse and execute without ValueError."""
        integration = ROOT / "docs/integration.md"
        blocks = extract_blocks_from_file(
            integration, start_line=309, end_line=340, root=ROOT
        )
        self.assertGreaterEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.language, "sh")
        # Ensure commands are joined
        joined_init_cmds = [cmd for _, cmd in block.commands if "init" in cmd]
        self.assertEqual(len(joined_init_cmds), 1)
        self.assertIn("--task investigate-bug", joined_init_cmds[0])
        self.assertIn("--output ./audit-workspace", joined_init_cmds[0])

        for line_no, cmd in block.commands:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Integration snippet at L{line_no} failed: {msg}")

    def test_cli_file_arguments_route_to_runner(self):
        """Passing file arguments limits verification to the specified target files."""
        # Verifying only docs/runbooks.md
        errors = verify_all_snippets(root=ROOT, files=["docs/runbooks.md"])
        self.assertEqual([], errors, f"Single file verification failed: {errors}")

        # Verifying with Windows backslashes
        errors_win = verify_all_snippets(root=ROOT, files=["docs\\runbooks.md"])
        self.assertEqual(
            [], errors_win, f"Windows path verification failed: {errors_win}"
        )

        # Verifying non-existent file reports an error for that file only
        errors = verify_all_snippets(root=ROOT, files=["nonexistent_file.md"])
        self.assertEqual(1, len(errors))
        self.assertIn("nonexistent_file.md", errors[0])

    def test_shell_syntax_error_safety(self):
        """Unclosed quotes or shell syntax anomalies must be handled safely without unhandled exceptions."""
        ok, msg = execute_snippet_command(
            'python -c "unclosed quote',
            root=ROOT,
        )
        self.assertFalse(ok)
        self.assertIn("Failed to parse command syntax", msg)


if __name__ == "__main__":
    unittest.main()
