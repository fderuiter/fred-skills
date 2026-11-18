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
        """The test suite must extract and execute shell command snippets from README.md, CONTRIBUTING.md, and docs/usage.md."""
        self.assertEqual((1, None), DOCUMENTED_RANGES["README.md"])
        self.assertEqual((1, None), DOCUMENTED_RANGES["docs/usage.md"])
        usage_blocks = discover_doc_blocks(root=ROOT)["docs/usage.md"]
        usage_cmds = [cmd for block in usage_blocks for _, cmd in block.commands]
        self.assertTrue(
            any("tar -xf" in cmd and ".agents/skills/" in cmd for cmd in usage_cmds),
            f"Expected tar -xf .agents/skills/ snippet in docs/usage.md, got: {usage_cmds}",
        )
        errors = verify_all_snippets(root=ROOT)
        self.assertEqual(
            [], errors, f"Documentation snippet verification failed: {errors}"
        )

    def test_usage_doc_executed_by_default_and_rejects_singular_agent_path(self):
        """verify_all_snippets executes docs/usage.md by default and rejects .agent/skills/ tar paths."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            for rel in (
                "README.md",
                "CONTRIBUTING.md",
                "AGENTS.md",
                "Makefile",
                "requirements-dev.txt",
                ".pre-commit-config.yaml",
                ".claude-plugin/marketplace.json",
                "catalog/hosts.json",
                "docs/toolkit.md",
                "docs/runbooks.md",
                "docs/integration.md",
            ):
                src = ROOT / rel
                dst = tmp_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
            # Symlink scripts and examples so dry-run checks resolve without copying everything
            (tmp_root / "scripts").symlink_to(
                ROOT / "scripts", target_is_directory=True
            )
            (tmp_root / "skills").symlink_to(ROOT / "skills", target_is_directory=True)
            (tmp_root / "examples").symlink_to(
                ROOT / "examples", target_is_directory=True
            )

            broken_usage = (
                (ROOT / "docs/usage.md")
                .read_text(encoding="utf-8")
                .replace(
                    "/path/to/project/.agents/skills/",
                    "/path/to/project/.agent/skills/",
                )
            )
            (tmp_root / "docs/usage.md").write_text(broken_usage, encoding="utf-8")

            errors = verify_all_snippets(root=tmp_root)
            self.assertTrue(
                any(
                    "docs/usage.md:" in err and ".agent/skills/" in err
                    for err in errors
                ),
                f"Expected docs/usage.md .agent/skills/ failure in default run, got: {errors}",
            )

    def test_tar_flags_and_host_path_validation(self):
        """tar validator accepts -xf/-tf/-xzf/-ztf and valid/custom paths while rejecting .agent/skills/."""
        valid_cmds = [
            "tar -xf fred-skills-0.2.0-starter.tar -C /path/to/project/.agents/skills/",
            "tar -tf fred-skills-0.2.0-starter.tar",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/",
            "tar -ztf repo-audit-concurrency.tar.gz",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.claude/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.gemini/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C ~/.gemini/antigravity/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/harness/skills/",
        ]
        for cmd in valid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected tar command to pass: {cmd} -> {msg}")

        invalid_cmds = [
            "tar -xf fred-skills-0.2.0-starter.tar -C /path/to/project/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C .agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C ~/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/skills/",
            "tar -cf archive.tar skills/",
            "tar -xf fred-skills-0.2.0-starter.tar -C",
        ]
        for cmd in invalid_cmds:
            ok, _ = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Expected tar command to fail: {cmd}")

        # Custom root with custom catalog/hosts.json
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_root = Path(tmp_dir)
            (custom_root / "catalog").mkdir(parents=True)
            (custom_root / "catalog/hosts.json").write_text(
                '{"schema_version": "1.0", "hosts": {"custom": {"project_path": ".custom-agent/skills", "user_path": ".custom-agent/skills"}}}',
                encoding="utf-8",
            )
            ok_custom, msg_custom = execute_snippet_command(
                "tar -xf pack.tar -C /path/to/project/.custom-agent/skills/",
                root=custom_root,
            )
            self.assertTrue(ok_custom, msg_custom)
            ok_unconfigured, _ = execute_snippet_command(
                "tar -xf pack.tar -C /path/to/project/.agent/skills/",
                root=custom_root,
            )
            self.assertFalse(ok_unconfigured)

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

    def test_documented_ranges_cover_all_seven_guides_completely(self) -> None:
        """All 7 registered guides use full-file (1, None) bounds."""
        expected_guides = {
            "README.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "docs/toolkit.md",
            "docs/usage.md",
            "docs/runbooks.md",
            "docs/integration.md",
        }
        self.assertEqual(expected_guides, set(DOCUMENTED_RANGES.keys()))
        for guide, bounds in DOCUMENTED_RANGES.items():
            self.assertEqual(
                (1, None),
                bounds,
                f"Expected full-file (1, None) bounds for {guide}, got {bounds}",
            )

    def test_default_verify_all_snippets_executes_all_seven_guides(self) -> None:
        """Default verify_all_snippets executes commands in AGENTS.md, docs/toolkit.md, and docs/usage.md."""
        executed_targets: list[str] = []
        orig_tar = TOOL_DISPATCHERS[("tar",)]
        orig_py = TOOL_DISPATCHERS[("python",)]

        def spy_tar(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            executed_targets.append(cmd)
            return orig_tar(parts, root, cmd)

        def spy_py(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            executed_targets.append(cmd)
            return orig_py(parts, root, cmd)

        TOOL_DISPATCHERS[("tar",)] = spy_tar
        TOOL_DISPATCHERS[("python",)] = spy_py
        try:
            errors = verify_all_snippets(root=ROOT)
            self.assertEqual([], errors)
        finally:
            TOOL_DISPATCHERS[("tar",)] = orig_tar
            TOOL_DISPATCHERS[("python",)] = orig_py

        # AGENTS.md has package_skills.py --output /tmp/release-verify
        self.assertTrue(
            any("package_skills.py" in cmd for cmd in executed_targets),
            "Expected AGENTS.md snippets to execute on default run",
        )
        # docs/toolkit.md has audit_to_sarif.py
        self.assertTrue(
            any("audit_to_sarif.py" in cmd for cmd in executed_targets),
            "Expected docs/toolkit.md snippets to execute on default run",
        )
        # docs/usage.md has tar -xf fred-skills-0.2.0-starter.tar / complete.tar
        self.assertTrue(
            any("fred-skills-0.2.0-starter.tar" in cmd for cmd in executed_targets),
            "Expected docs/usage.md starter.tar snippet to execute on default run",
        )
        self.assertTrue(
            any("fred-skills-0.2.0-complete.tar" in cmd for cmd in executed_targets),
            "Expected docs/usage.md complete.tar snippet to execute on default run",
        )

    def test_tar_validation_enforces_compression_and_rejects_unknown_flags(
        self,
    ) -> None:
        """_validate_tar accepts -xf/-tf on .tar and -xzf/-ztf on .tar.gz, rejecting mismatches and unknown flags."""
        valid_cmds = [
            "tar -xf fred-skills-0.2.0-starter.tar -C /path/to/project/.agents/skills/",
            "tar -xf fred-skills-0.2.0-complete.tar -C /path/to/project/.agents/skills/",
            "tar -tf fred-skills-0.2.0-starter.tar",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/",
            "tar -ztf repo-audit-concurrency.tar.gz",
        ]
        for cmd in valid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected valid tar command to pass: {cmd} -> {msg}")

        invalid_cmds = [
            "tar -xzf fred-skills-0.2.0-starter.tar -C /path/to/project/.agents/skills/",
            "tar -ztf fred-skills-0.2.0-complete.tar",
            "tar -xf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/",
            "tar -tf fred-skills-suite.tar.gz",
            "tar -xzf repo-audit-concurrency.tar.gz --invalid-flag",
            "tar -xf fred-skills-0.2.0-starter.tar --unknown-option",
            "tar -czf out.tar.gz skills/",
        ]
        for cmd in invalid_cmds:
            ok, _ = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Expected invalid tar command to fail: {cmd}")

    def test_invalid_cli_flag_probes_fail_across_all_dispatchers(self) -> None:
        """Invalid CLI flags across make, python, ruff, pre-commit, sha256sum, git, curl, gh, and marketplace must fail."""
        invalid_probes = [
            "make check --invalid-flag",
            "make --nonexistent-option",
            "python --invalid-flag scripts/validate_skills.py",
            "python -m venv .venv --invalid-flag",
            "python -m pip install -r requirements-dev.txt --invalid-flag",
            "python -m unittest discover -s tests -v --invalid-flag",
            "python -m json.tool receipt.json --invalid-flag",
            "python -m mypy --invalid-flag",
            "python scripts/sync_audit_resources.py --check --invalid-flag",
            "python scripts/sync_audit_resources.py --invalid-flag",
            "python scripts/validate_skills.py --invalid-flag",
            "python scripts/package_skills.py --output /tmp/release-verify --invalid-flag",
            "python scripts/run_checks.py --invalid-flag",
            "python scripts/check_doc_links.py --check",
            "python scripts/check_doc_links.py --invalid-flag",
            "python scripts/verify_doc_snippets.py --invalid-flag",
            "python scripts/verify_workflow_actions.py --invalid-flag",
            "python scripts/audit_to_sarif.py audit.json --output out.sarif --invalid-flag",
            "python examples/walkthrough/run_demo.py --output audit-output/demo --invalid-flag",
            "python skills/repo-audit-program/scripts/validate_audit.py audit.json --invalid-flag",
            "ruff format . --invalid-flag",
            "ruff check . --invalid-flag",
            "pre-commit run --all-files --invalid-flag",
            "pre-commit install --invalid-flag",
            "sha256sum --ignore-missing -c checksums.sha256 --invalid-flag",
            "git status --invalid-flag",
            "git -C /path/to/repository rev-parse --invalid-flag",
            "git -C /path/to/repository fetch --invalid-flag",
            "git --invalid-flag status",
            "curl -LO --invalid-flag https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/SHA256SUMS",
            "gh attestation verify repo-audit-concurrency.tar.gz --repo fderuiter/fred-skills --invalid-flag",
            "/plugin marketplace add fderuiter/fred-skills --invalid-flag",
            "/plugin install complete@fred-skills --invalid-flag",
            "codex plugin add starter@fred-skills --invalid-flag",
        ]
        for probe in invalid_probes:
            ok, _ = execute_snippet_command(probe, root=ROOT)
            self.assertFalse(ok, f"Invalid-flag probe unexpectedly passed: {probe}")

    def test_shell_operators_pipes_and_redirections_split_before_dispatch(
        self,
    ) -> None:
        """Logical operators, pipes, and redirections split cleanly while preserving quotes and placeholders."""
        valid_compound = [
            'python -m json.tool /path/to/project/.fred-skills/<host>-<scope>/receipt.json > /dev/null && echo "Valid"',
            "lsof /path/to/project/.fred-skills/<host>-<scope>/operation.lock 2>/dev/null || true",
            "which git && git --version",
            "git -C /path/to/repository fetch --unshallow || git -C /path/to/repository fetch --deepen=100 origin main",
            'grep -E "(foo|bar)" file.txt || echo "done"',
        ]
        for cmd in valid_compound:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Valid compound command failed: {cmd} -> {msg}")

        invalid_compound = [
            "which git && git --invalid-flag",
            "python -m json.tool receipt.json > /dev/null && make check --invalid-flag",
            "echo start | python scripts/fred.py search --invalid-flag",
        ]
        for cmd in invalid_compound:
            ok, _ = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Invalid compound command unexpectedly passed: {cmd}")

    def test_make_dispatcher_fallback_without_gnu_make(self) -> None:
        """_validate_make validates both flags and Makefile targets when GNU make is unavailable."""
        from unittest.mock import patch

        with patch("verify_doc_snippets.shutil.which", return_value=None):
            ok_valid, msg_valid = execute_snippet_command("make check", root=ROOT)
            self.assertTrue(ok_valid, msg_valid)

            ok_bad_flag, _ = execute_snippet_command(
                "make check --invalid-flag", root=ROOT
            )
            self.assertFalse(ok_bad_flag)

            ok_bad_target, _ = execute_snippet_command("make bogus_target", root=ROOT)
            self.assertFalse(ok_bad_target)

    def test_agents_md_includes_doc_verification_scripts(self) -> None:
        """AGENTS.md fallback command block includes check_doc_links.py and verify_doc_snippets.py."""
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("python scripts/check_doc_links.py", agents_text)
        self.assertIn("python scripts/verify_doc_snippets.py", agents_text)


if __name__ == "__main__":
    unittest.main()
