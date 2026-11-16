"""Tests for documentation verification harnesses: relative links and CLI snippet verification."""

import inspect
import shlex
import subprocess
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

    def test_cli_rejects_removed_check_flag(self):
        """CLI parser for check_doc_links must reject the removed --check flag."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_doc_links.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("unrecognized arguments: --check", proc.stderr)

    def test_cli_accepts_file_arguments(self):
        """CLI for check_doc_links accepts specific files without --check flag."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/check_doc_links.py"), "README.md"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(
            "PASS: Relative links verified across 1 markdown documentation files.",
            proc.stdout,
        )


class DocSnippetVerificationTests(unittest.TestCase):
    """Test discovery, extraction, and execution of documentation command snippets."""

    def test_discover_command_blocks_across_all_five_targets(self):
        """The test harness discovers command blocks across all configured documentation targets."""
        discovered = discover_doc_blocks(root=ROOT)
        self.assertEqual(
            set(DOCUMENTED_RANGES.keys()),
            set(discovered.keys()),
            "Must discover blocks in all specified documentation files",
        )

        for rel_path, (s_line, e_line) in DOCUMENTED_RANGES.items():
            blocks = discovered[rel_path]
            self.assertTrue(
                len(blocks) > 0,
                f"Expected discovered blocks in {rel_path} (L{s_line}-L{e_line})",
            )
            for block in blocks:
                if s_line is not None:
                    self.assertGreaterEqual(block.end_line, s_line)
                if e_line is not None:
                    self.assertLessEqual(block.start_line, e_line)

        # Full-file discovery coverage for runbooks (65 blocks) and integration (32 blocks)
        self.assertEqual(
            len(discovered["docs/runbooks.md"]),
            65,
            "Must discover all 65 code blocks in docs/runbooks.md",
        )
        self.assertEqual(
            len(discovered["docs/integration.md"]),
            32,
            "Must discover all 32 code blocks in docs/integration.md",
        )

    def test_toolkit_cli_subcommands_against_fred_lifecycle_groups(self):
        """Documented subcommands from docs/toolkit.md must match scripts/fred.py LIFECYCLE_GROUPS dynamically."""
        errors = verify_toolkit_subcommands(root=ROOT)
        self.assertEqual(
            [], errors, f"Toolkit subcommand verification failed: {errors}"
        )

        # Dynamic verification of bidirectional parity against LIFECYCLE_GROUPS
        groups = get_registered_fred_subcommands(root=ROOT)
        registered = {cmd for cmds in groups.values() for cmd in cmds}

        # Assert complete coverage of all 26 registered subcommands
        self.assertEqual(
            len(registered),
            26,
            f"Expected exactly 26 registered subcommands across lifecycle groups, found {len(registered)}: {sorted(registered)}",
        )

        start_line, end_line = DOCUMENTED_RANGES.get("docs/toolkit.md", (10, 195))
        blocks = extract_blocks_from_file(
            ROOT / "docs/toolkit.md", start_line, end_line, root=ROOT
        )
        documented: set[str] = set()
        for block in blocks:
            for _, cmd in block.commands:
                if "scripts/fred.py" not in cmd:
                    continue
                parts = shlex.split(cmd)
                for idx, part in enumerate(parts):
                    if part.endswith("fred.py") and idx + 1 < len(parts):
                        subcmd = parts[idx + 1]
                        if not subcmd.startswith("-"):
                            documented.add(subcmd)

        self.assertEqual(
            registered,
            documented,
            f"Discrepancy between registered and documented subcommands: missing={sorted(registered - documented)}, unexpected={sorted(documented - registered)}",
        )

    def test_omitting_subcommand_from_docs_fails_verification(self):
        """Omitting any registered subcommand from docs/toolkit.md causes verify_toolkit_subcommands to fail."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            docs_dir = tmp_root / "docs"
            docs_dir.mkdir(parents=True)
            original_toolkit = (ROOT / "docs/toolkit.md").read_text(encoding="utf-8")
            # Create a toolkit.md that omits triage snippets
            omitted_lines = [
                line
                for line in original_toolkit.splitlines(keepends=True)
                if "triage" not in line
            ]
            (docs_dir / "toolkit.md").write_text(
                "".join(omitted_lines), encoding="utf-8"
            )

            errors = verify_toolkit_subcommands(root=tmp_root)
            self.assertTrue(
                any(
                    "Registered subcommand 'triage' lacks documentation snippets" in err
                    for err in errors
                ),
                f"Expected error for missing triage subcommand, got: {errors}",
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

    def test_verify_all_snippets_signature_excludes_target_files(self):
        """verify_all_snippets signature accepts root and files, excluding redundant target_files."""
        sig = inspect.signature(verify_all_snippets)
        self.assertIn("files", sig.parameters)
        self.assertNotIn("target_files", sig.parameters)

    def test_shell_syntax_error_safety(self):
        """Unclosed quotes or shell syntax anomalies must be handled safely without unhandled exceptions."""
        ok, msg = execute_snippet_command(
            'python -c "unclosed quote',
            root=ROOT,
        )
        self.assertFalse(ok)
        self.assertIn("Failed to parse command syntax", msg)

    def test_multiline_python_commands_in_runbooks(self):
        """Multiline Python commands in docs/runbooks.md:L84-L93 and L409-L449 must parse without quotation errors."""
        runbooks = ROOT / "docs/runbooks.md"
        # Test L84-L93
        blocks_early = extract_blocks_from_file(
            runbooks, start_line=84, end_line=94, root=ROOT
        )
        self.assertEqual(1, len(blocks_early))
        self.assertTrue(len(blocks_early[0].commands) > 0)
        line_no, cmd = blocks_early[0].commands[0]
        self.assertEqual(line_no, 84)
        ok, msg = execute_snippet_command(cmd, root=ROOT)
        self.assertTrue(ok, f"Runbooks snippet at L{line_no} failed: {msg}")

        # Test L409-L449
        blocks_late = extract_blocks_from_file(
            runbooks, start_line=408, end_line=450, root=ROOT
        )
        self.assertEqual(1, len(blocks_late))
        self.assertTrue(len(blocks_late[0].commands) > 0)
        line_no, cmd = blocks_late[0].commands[0]
        self.assertEqual(line_no, 409)
        ok, msg = execute_snippet_command(cmd, root=ROOT)
        self.assertTrue(ok, f"Runbooks snippet at L{line_no} failed: {msg}")

    def test_cli_placeholder_tokens_normalization(self):
        """CLI commands with placeholder tokens (<host>, <scope>, <skill-name>, etc.) must pass argument parsing."""
        sample_cmds = [
            "python scripts/fred.py recover-install --destination /path/to/project --host <host> --confirm-interrupted",
            "python scripts/fred.py install --destination /path/to/project --host <host> --scope <scope> --pack starter",
            "python scripts/fred.py remove --destination /path/to/project --host <host> --skill <skill-name>",
            "python scripts/fred.py plan /path/to/repository --task review-pr --mode changes --base '<base>'",
        ]
        for cmd in sample_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(
                ok, f"Command with placeholder tokens failed: {cmd} -> {msg}"
            )

    def test_full_file_runbooks_and_integration_snippets_pass(self):
        """Full-file snippet verification across docs/runbooks.md (65 blocks) and docs/integration.md (32 blocks) passes with zero errors."""
        errors = verify_all_snippets(
            root=ROOT, files=["docs/runbooks.md", "docs/integration.md"]
        )
        self.assertEqual([], errors, f"Full-file verification failed: {errors}")


if __name__ == "__main__":
    unittest.main()
