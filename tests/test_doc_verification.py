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

from unittest.mock import patch

from check_doc_links import check_docs, check_file_links, extract_relative_links
from skill_tools import (
    extract_relative_links as skill_tools_extract_relative_links,
)
from skill_tools import (
    resolve_relative_link_target,
)
from verify_doc_snippets import (
    DOCUMENTED_RANGES,
    TOOL_DISPATCHERS,
    clear_subprocess_cache,
    discover_doc_blocks,
    execute_snippet_command,
    extract_blocks_from_file,
    find_tool_dispatcher,
    get_registered_fred_subcommands,
    register_tool_dispatcher,
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

    def test_extract_relative_links_exported_from_skill_tools(self):
        """extract_relative_links is exported from skill_tools and shared with check_doc_links."""
        self.assertIs(extract_relative_links, skill_tools_extract_relative_links)

    def test_extract_relative_links_supports_html_images_and_references(self):
        """Standard markdown links, inline images, HTML link tags, and reference links are extracted."""
        content = (
            "Markdown [guide](docs/guide.md) and image ![diagram](images/flow.png).\n"
            'HTML link <a href="docs/api.md">API</a> and image <img src="images/logo.svg" />.\n'
            "Reference [ref-doc] defined later.\n"
            "[ref-doc]: docs/reference.md\n"
        )
        links = extract_relative_links(content)
        self.assertIn("docs/guide.md", links)
        self.assertIn("images/flow.png", links)
        self.assertIn("docs/api.md", links)
        self.assertIn("images/logo.svg", links)
        self.assertIn("docs/reference.md", links)

    def test_resolve_relative_link_target_path_resolution(self):
        """Relative link targets resolve to absolute Paths against base directories."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir).resolve()
            self.assertEqual(
                base_dir / "target.md",
                resolve_relative_link_target("target.md", base_dir),
            )
            self.assertEqual(
                base_dir / "sub" / "doc.md",
                resolve_relative_link_target("sub/doc.md", base_dir),
            )
            self.assertEqual(
                base_dir.parent / "sibling.md",
                resolve_relative_link_target("../sibling.md", base_dir),
            )

    def test_resolve_relative_link_target_anchor_and_query_stripping(self):
        """Anchors and queries are stripped from link targets; fragment-only links return None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir).resolve()
            self.assertEqual(
                base_dir / "guide.md",
                resolve_relative_link_target("guide.md#overview", base_dir),
            )
            self.assertEqual(
                base_dir / "api.md",
                resolve_relative_link_target("api.md?version=2#endpoint", base_dir),
            )
            self.assertIsNone(resolve_relative_link_target("#heading", base_dir))
            self.assertIsNone(resolve_relative_link_target("", base_dir))
            self.assertIsNone(resolve_relative_link_target("   ", base_dir))

    def test_resolve_relative_link_target_percent_decoding(self):
        """Percent-encoded characters in paths are properly unquoted."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir).resolve()
            self.assertEqual(
                base_dir / "my document.md",
                resolve_relative_link_target("my%20document.md", base_dir),
            )
            self.assertEqual(
                base_dir / "folder with spaces" / "doc.md",
                resolve_relative_link_target(
                    "folder%20with%20spaces/doc.md#intro", base_dir
                ),
            )

    def test_resolve_relative_link_target_ignores_external_schemes(self):
        """External links with schemes or network locations return None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir).resolve()
            self.assertIsNone(
                resolve_relative_link_target("https://example.com/guide.md", base_dir)
            )
            self.assertIsNone(
                resolve_relative_link_target("http://example.com/page", base_dir)
            )
            self.assertIsNone(
                resolve_relative_link_target("//example.com/path", base_dir)
            )
            self.assertIsNone(
                resolve_relative_link_target("mailto:user@example.com", base_dir)
            )

    def test_resolve_relative_link_target_exported_from_skill_tools(self):
        """resolve_relative_link_target is exported from skill_tools and shared."""
        import check_doc_links
        import skill_tools

        self.assertIn("resolve_relative_link_target", skill_tools.__all__)
        self.assertIs(
            resolve_relative_link_target,
            check_doc_links.resolve_relative_link_target,
        )

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

    def test_toolkit_cli_subcommands_against_fred_lifecycle_groups(self) -> None:
        """Documented subcommands from docs/toolkit.md must match scripts/fred.py LIFECYCLE_GROUPS dynamically."""
        errors = verify_toolkit_subcommands(root=ROOT)
        self.assertEqual(
            [], errors, f"Toolkit subcommand verification failed: {errors}"
        )

        # Dynamic verification of bidirectional parity against LIFECYCLE_GROUPS
        groups = get_registered_fred_subcommands(root=ROOT)
        registered = {cmd for cmds in groups.values() for cmd in cmds}

        # Assert complete coverage of all 27 registered subcommands
        self.assertEqual(
            len(registered),
            27,
            f"Expected exactly 27 registered subcommands across lifecycle groups, found {len(registered)}: {sorted(registered)}",
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

        # Invalid run_checks.py phase
        ok, msg = execute_snippet_command(
            "python scripts/run_checks.py --phase nonexistent_phase",
            root=ROOT,
        )
        self.assertFalse(ok)
        self.assertIn("run_checks.py failed", msg)

    def test_repeated_readonly_subprocess_checks_are_cached(self) -> None:
        """Repeated read-only subprocess checks in verify_doc_snippets execute once and hit cache."""
        clear_subprocess_cache()
        real_run = subprocess.run
        spawned: list[list[str]] = []

        def counting_run(
            cmd: list[str], *args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            spawned.append(list(cmd))
            return real_run(cmd, *args, **kwargs)  # type: ignore[call-overload,no-any-return]

        commands = [
            "ruff check .",
            "ruff format --check .",
            "python -m mypy",
            "python scripts/sync_audit_resources.py --check",
            "python scripts/validate_skills.py",
            "python scripts/run_checks.py --phase lint",
            "python scripts/run_checks.py --phase typecheck",
        ]
        try:
            with patch("verify_doc_snippets.subprocess.run", side_effect=counting_run):
                for _ in range(2):
                    for cmd in commands:
                        ok, msg = execute_snippet_command(cmd, root=ROOT)
                        self.assertTrue(ok, f"{cmd} failed: {msg}")
            self.assertEqual(
                len(commands),
                len(spawned),
                f"Expected {len(commands)} unique subprocess spawns across 2 passes, got {len(spawned)}",
            )
        finally:
            clear_subprocess_cache()

    def test_marketplace_plugin_install_validation(self):
        """Marketplace registration and plugin install commands validate against .claude-plugin/marketplace.json."""
        valid_cmds = [
            "/plugin marketplace add fderuiter/fred-skills",
            "/plugin install complete@fred-skills",
            "/plugin install starter@fred-skills",
            "/plugin install repo-audit-concurrency@fred-skills",
            "codex plugin marketplace add fderuiter/fred-skills",
            "codex plugin add starter@fred-skills",
            "codex plugin add complete@fred-skills",
            "codex plugin add repo-audit-concurrency@fred-skills",
        ]
        for cmd in valid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected valid marketplace command '{cmd}': {msg}")

        invalid_cmds = [
            ("/plugin install unknown-pack@fred-skills", "Unknown marketplace plugin"),
            (
                "codex plugin add unknown-skill@fred-skills",
                "Unknown marketplace plugin",
            ),
            ("/plugin install complete@other-market", "Unexpected marketplace"),
            ("/plugin install complete", "requires '<name>@fred-skills'"),
            ("codex plugin add starter", "requires '<name>@fred-skills'"),
            ("/plugin marketplace add other/repo", "Unexpected plugin id"),
            ("/plugin remove complete@fred-skills", "Unsupported marketplace plugin"),
            (
                "codex plugin install starter@fred-skills",
                "Unsupported marketplace plugin",
            ),
        ]
        for cmd, expected_err in invalid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(
                ok, f"Expected invalid marketplace command '{cmd}' to fail"
            )
            self.assertIn(expected_err, msg)

    def test_incomplete_marketplace_add_blocks_rejected(self):
        """Marketplace add blocks in README.md, docs/usage.md, and docs/integration.md must include an install step."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            (tmp_root / ".claude-plugin").mkdir(parents=True)
            (tmp_root / ".claude-plugin/marketplace.json").write_bytes(
                (ROOT / ".claude-plugin/marketplace.json").read_bytes()
            )
            (tmp_root / "docs").mkdir(parents=True)
            for rel_path in ("README.md", "docs/usage.md", "docs/integration.md"):
                content = (ROOT / rel_path).read_text(encoding="utf-8")
                stripped = "\n".join(
                    line
                    for line in content.splitlines()
                    if "/plugin install " not in line
                    and "codex plugin add " not in line
                )
                (tmp_root / rel_path).write_text(stripped + "\n", encoding="utf-8")

            for rel_path in ("README.md", "docs/usage.md", "docs/integration.md"):
                errors = verify_all_snippets(root=tmp_root, files=[rel_path])
                self.assertTrue(
                    any("missing '/plugin install" in err for err in errors),
                    f"Expected missing Claude install error for {rel_path}, got: {errors}",
                )
                self.assertTrue(
                    any("missing 'codex plugin add" in err for err in errors),
                    f"Expected missing Codex install error for {rel_path}, got: {errors}",
                )

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

    def test_unknown_shell_command_passes_without_false_positive(self):
        """Unknown shell commands pass without false positive failures when no tool matches."""
        ok, msg = execute_snippet_command("echo 'hello world'", root=ROOT)
        self.assertTrue(ok)
        self.assertEqual("", msg)

        ok2, msg2 = execute_snippet_command("ls -la /tmp", root=ROOT)
        self.assertTrue(ok2)
        self.assertEqual("", msg2)

    def test_custom_tool_dispatcher_registration_and_dispatch(self):
        """Developers can register custom tool validation handlers via prefix matching."""
        invoked_commands = []

        def custom_handler(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            invoked_commands.append(cmd)
            if "--fail" in parts:
                return False, "custom failure"
            return True, ""

        saved_dispatchers = dict(TOOL_DISPATCHERS)
        try:
            register_tool_dispatcher("custom-tool", custom_handler)
            self.assertIsNotNone(find_tool_dispatcher(["custom-tool", "run"]))

            ok, msg = execute_snippet_command("custom-tool run --fast", root=ROOT)
            self.assertTrue(ok)
            self.assertEqual("", msg)
            self.assertEqual(invoked_commands, ["custom-tool run --fast"])

            ok_fail, msg_fail = execute_snippet_command(
                "custom-tool run --fail", root=ROOT
            )
            self.assertFalse(ok_fail)
            self.assertEqual("custom failure", msg_fail)
        finally:
            TOOL_DISPATCHERS.clear()
            TOOL_DISPATCHERS.update(saved_dispatchers)

    def test_cyclomatic_complexity_below_threshold(self):
        """All verifier functions maintain cyclomatic complexity strictly below 10."""
        import ast

        def _cyclomatic_complexity(node: ast.AST) -> int:
            count = 1
            for child in ast.walk(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.ExceptHandler,
                        ast.With,
                        ast.AsyncWith,
                    ),
                ):
                    count += 1
                elif isinstance(child, ast.BoolOp):
                    count += len(child.values) - 1
                elif isinstance(child, (ast.IfExp, ast.Assert)):
                    count += 1
            return count

        src_path = ROOT / "scripts/verify_doc_snippets.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for item in tree.body:
            if isinstance(item, ast.FunctionDef):
                complexity = _cyclomatic_complexity(item)
                self.assertLess(
                    complexity,
                    10,
                    f"Function '{item.name}' cyclomatic complexity {complexity} exceeds limit of 10",
                )

    def test_markdown_parsing_nesting_depth(self):
        """Markdown parsing functions maintain nesting depth of 3 or fewer levels."""
        import ast

        def _max_nesting_depth(node: ast.AST, current_depth: int = 0) -> int:
            depths = [current_depth]
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child,
                    (
                        ast.If,
                        ast.While,
                        ast.For,
                        ast.AsyncFor,
                        ast.With,
                        ast.AsyncWith,
                        ast.Try,
                    ),
                ):
                    depths.append(_max_nesting_depth(child, current_depth + 1))
                else:
                    depths.append(_max_nesting_depth(child, current_depth))
            return max(depths)

        src_path = ROOT / "scripts/verify_doc_snippets.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        parsing_functions = {
            "_is_block_in_range",
            "_merge_command_lines",
            "_extract_commands_from_block",
            "_parse_fenced_blocks",
            "extract_blocks_from_file",
        }
        for item in tree.body:
            if isinstance(item, ast.FunctionDef) and item.name in parsing_functions:
                depth = _max_nesting_depth(item)
                self.assertLessEqual(
                    depth,
                    3,
                    f"Function '{item.name}' nesting depth {depth} exceeds limit of 3",
                )


if __name__ == "__main__":
    unittest.main()
