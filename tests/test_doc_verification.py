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
    verify_runbook_citations,
    verify_toolkit_subcommands,
)


class DocLinkVerificationTests(unittest.TestCase):
    """Test verification of relative links in repository documentation."""

    def test_repository_relative_links_resolve(self):
        """All relative file links across repository documentation files must resolve."""
        errors = check_docs(root=ROOT)
        self.assertEqual([], errors, f"Found broken relative links: {errors}")

    def test_extract_relative_links_filters_external_and_fragments(self):
        """External URLs and in-page anchor links must be ignored by default, with opt-in fragments."""
        content = (
            "Check [Section](#heading) and [Site](https://example.com/page).\n"
            "Also [Local doc](docs/experience.md) and [Target](docs/toolkit.md#discover).\n"
        )
        links = extract_relative_links(content)
        self.assertIn("docs/experience.md", links)
        self.assertIn("docs/toolkit.md#discover", links)
        self.assertNotIn("#heading", links)
        self.assertNotIn("https://example.com/page", links)

        with_fragments = extract_relative_links(content, include_fragments=True)
        self.assertIn("#heading", with_fragments)
        self.assertIn("docs/toolkit.md#discover", with_fragments)
        self.assertNotIn("https://example.com/page", with_fragments)

    def test_cli_subprocess_verifiers_and_docs_phase(self) -> None:
        """CLI subprocess entry points validate explorer.html, reject broken anchors/snippets, and wire run_checks docs phase."""
        ok_explorer = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/check_doc_links.py"),
                "docs/explorer.html",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, ok_explorer.returncode, ok_explorer.stderr)

        ok_snippets = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/verify_doc_snippets.py"),
                "--check",
                "docs/integration.md",
                "docs/runbooks.md",
                "docs/api.md",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, ok_snippets.returncode, ok_snippets.stderr)
        self.assertIn("PASS:", ok_snippets.stdout)

        dry_docs = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_checks.py"),
                "--phase",
                "docs",
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, dry_docs.returncode, dry_docs.stderr)
        self.assertIn("scripts/check_doc_links.py", dry_docs.stdout)
        self.assertIn("scripts/verify_doc_snippets.py", dry_docs.stdout)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            bad_link_doc = tmp_path / "broken_anchor.md"
            bad_link_doc.write_text(
                "# Valid Heading\n\nSee [Missing](#missing-section).\n",
                encoding="utf-8",
            )
            bad_link_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/check_doc_links.py"),
                    str(bad_link_doc),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, bad_link_proc.returncode)
            self.assertIn("#missing-section", bad_link_proc.stderr)

            bad_snippet_doc = tmp_path / "broken_snippet.md"
            bad_snippet_doc.write_text(
                "```sh\npython scripts/fred.py plan . --output-plan out.json\n```\n",
                encoding="utf-8",
            )
            bad_snippet_proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/verify_doc_snippets.py"),
                    str(bad_snippet_doc),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(0, bad_snippet_proc.returncode)
            self.assertIn("--output-plan", bad_snippet_proc.stderr)

    def test_in_page_and_cross_file_anchor_validation(self) -> None:
        """check_file_links and check_docs validate Markdown and HTML in-page and cross-file #fragment anchors."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            guide = tmp_path / "guide.md"
            guide.write_text(
                "# Guide Overview\n\n"
                "## Module: `fredlib.runs`\n\n"
                "### `import_sarif`\n\n"
                "## Duplicate\n\n"
                "## Duplicate\n\n"
                "```md\n# Ignored Fence Heading\n[Ignored](#not-real)\n```\n",
                encoding="utf-8",
            )
            explorer = tmp_path / "explorer.html"
            explorer.write_text(
                "<!doctype html><html><body>"
                '<article id="repo-audit-concurrency"><a href="guide.md#module-fredlibruns">Runs</a></article>'
                '<a href="#repo-audit-concurrency">Self</a>'
                "</body></html>\n",
                encoding="utf-8",
            )
            index_doc = tmp_path / "index.md"
            index_doc.write_text(
                "# Index\n\n"
                "- [Self](#index)\n"
                "- [Overview](guide.md#guide-overview)\n"
                "- [Func](guide.md#import_sarif)\n"
                "- [Dup 1](guide.md#duplicate)\n"
                "- [Dup 2](guide.md#duplicate-1)\n"
                "- [Card](explorer.html#repo-audit-concurrency)\n",
                encoding="utf-8",
            )
            self.assertEqual([], check_docs(root=tmp_path, target_files=[tmp_path]))

            broken_md = tmp_path / "broken.md"
            broken_md.write_text(
                "# Heading\n\n"
                "- [Bad in-page](#nonexistent-anchor)\n"
                "- [Bad fence heading](guide.md#ignored-fence-heading)\n"
                "- [Bad cross md](guide.md#missing-target)\n"
                "- [Bad cross html](explorer.html#missing-skill)\n",
                encoding="utf-8",
            )
            broken_errs = check_file_links(broken_md, root=tmp_path)
            self.assertEqual(4, len(broken_errs), broken_errs)
            self.assertTrue(any("#nonexistent-anchor" in e for e in broken_errs))
            self.assertTrue(any("#ignored-fence-heading" in e for e in broken_errs))
            self.assertTrue(any("#missing-target" in e for e in broken_errs))
            self.assertTrue(any("#missing-skill" in e for e in broken_errs))

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

    def test_orphan_guide_in_docs_is_rejected(self) -> None:
        """check_docs rejects unreferenced guides in docs/ and passes once linked."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            docs_dir = tmp_path / "docs"
            docs_dir.mkdir()
            (tmp_path / "README.md").write_text(
                "# Project\n\nSee [Guide](docs/linked.md).\n",
                encoding="utf-8",
            )
            (docs_dir / "linked.md").write_text("# Linked\n", encoding="utf-8")
            orphan = docs_dir / "orphan-notes.md"
            orphan.write_text(
                "# Orphan\n\nSelf link [Top](#orphan) does not count.\n",
                encoding="utf-8",
            )

            errors = check_docs(root=tmp_path)
            self.assertEqual(1, len(errors), errors)
            self.assertIn("docs/orphan-notes.md", errors[0])
            self.assertIn("Orphaned documentation file", errors[0])

            (docs_dir / "linked.md").write_text(
                "# Linked\n\nAlso see [Notes](orphan-notes.md).\n",
                encoding="utf-8",
            )
            self.assertEqual([], check_docs(root=tmp_path))

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

        guides_with_blocks = {
            "README.md",
            "CONTRIBUTING.md",
            "AGENTS.md",
            "docs/api.md",
            "docs/toolkit.md",
            "docs/usage.md",
            "docs/runbooks.md",
            "docs/integration.md",
        }
        self.assertTrue(guides_with_blocks.issubset(set(DOCUMENTED_RANGES.keys())))

        for rel_path, (s_line, e_line) in DOCUMENTED_RANGES.items():
            blocks = discovered[rel_path]
            if rel_path in guides_with_blocks:
                self.assertTrue(
                    len(blocks) > 0,
                    f"Expected discovered blocks in {rel_path} (L{s_line}-L{e_line})",
                )
            for block in blocks:
                if s_line is not None:
                    self.assertGreaterEqual(block.end_line, s_line)
                if e_line is not None:
                    self.assertLessEqual(block.start_line, e_line)

        # Full-file discovery coverage for api (42 blocks), runbooks (65 blocks), and integration (32 blocks)
        self.assertEqual(
            len(discovered["docs/api.md"]),
            42,
            "Must discover all 42 Python blocks in docs/api.md",
        )

        # Full-file discovery coverage for runbooks (80 blocks) and integration (37 blocks)
        self.assertEqual(
            len(discovered["docs/runbooks.md"]),
            80,
            "Must discover all 80 code blocks in docs/runbooks.md",
        )
        self.assertEqual(
            len(discovered["docs/integration.md"]),
            37,
            "Must discover all 37 code blocks in docs/integration.md",
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

        # Assert complete coverage of all 29 registered subcommands
        self.assertEqual(
            len(registered),
            29,
            f"Expected exactly 29 registered subcommands across lifecycle groups, found {len(registered)}: {sorted(registered)}",
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
        ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        valid_cmds = [
            f"tar -xf fred-skills-{ver}-starter.tar -C /path/to/project/.agents/skills/",
            f"tar -tf fred-skills-{ver}-starter.tar",
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
            f"tar -xf fred-skills-{ver}-starter.tar -C /path/to/project/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C .agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C ~/.agent/skills/",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/skills/",
            "tar -cf archive.tar skills/",
            f"tar -xf fred-skills-{ver}-starter.tar -C",
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
        self.assertNotIn(("codex", "plugin"), TOOL_DISPATCHERS)
        valid_cmds = [
            "/plugin marketplace add fderuiter/fred-skills",
            "/plugin install fred-skills@fred-skills",
            "/plugin install complete@fred-skills",
            "/plugin install starter@fred-skills",
            "/plugin install repo-audit-concurrency@fred-skills",
        ]
        for cmd in valid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected valid marketplace command '{cmd}': {msg}")

        invalid_cmds = [
            ("/plugin install unknown-pack@fred-skills", "Unknown marketplace plugin"),
            ("/plugin install complete@other-market", "Unexpected marketplace"),
            ("/plugin install complete", "requires '<name>@fred-skills'"),
            ("/plugin marketplace add other/repo", "Unexpected plugin id"),
            ("/plugin remove complete@fred-skills", "Unsupported marketplace plugin"),
        ]
        for cmd, expected_err in invalid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(
                ok, f"Expected invalid marketplace command '{cmd}' to fail"
            )
            self.assertIn(expected_err, msg)

    def test_incomplete_marketplace_add_blocks_rejected(self):
        """Marketplace add blocks in README.md, docs/usage.md, and docs/integration.md must include an install step and reject codex plugin."""
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
                )
                (tmp_root / rel_path).write_text(stripped + "\n", encoding="utf-8")

            for rel_path in ("README.md", "docs/usage.md", "docs/integration.md"):
                errors = verify_all_snippets(root=tmp_root, files=[rel_path])
                self.assertTrue(
                    any("missing '/plugin install" in err for err in errors),
                    f"Expected missing Claude install error for {rel_path}, got: {errors}",
                )

            (tmp_root / "README.md").write_text(
                "```sh\ncodex plugin marketplace add fderuiter/fred-skills\ncodex plugin add starter@fred-skills\n```\n",
                encoding="utf-8",
            )
            codex_errors = verify_all_snippets(root=tmp_root, files=["README.md"])
            self.assertTrue(
                any("Unsupported 'codex plugin'" in err for err in codex_errors),
                f"Expected codex plugin rejection, got: {codex_errors}",
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
        """Multiline commands with continuation backslashes in docs/integration.md (L356-L388) must parse and execute without ValueError."""
        integration = ROOT / "docs/integration.md"
        blocks = extract_blocks_from_file(
            integration, start_line=356, end_line=388, root=ROOT
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
        """Full-file snippet verification across docs/runbooks.md (80 blocks) and docs/integration.md (37 blocks) passes with zero errors."""
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

        def custom_handler(parts: list[str], _root: Path, cmd: str) -> tuple[bool, str]:
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
        """All root Markdown guides and docs/*.md files are discovered with full-file (1, None) bounds."""
        expected_guides = {p.name for p in ROOT.glob("*.md")} | {
            p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md")
        }
        self.assertGreaterEqual(len(expected_guides), 17)
        self.assertEqual(expected_guides, set(DOCUMENTED_RANGES.keys()))
        for guide, bounds in DOCUMENTED_RANGES.items():
            self.assertEqual(
                (1, None),
                bounds,
                f"Expected full-file (1, None) bounds for {guide}, got {bounds}",
            )

    def test_default_verify_all_snippets_executes_all_seven_guides(self) -> None:
        """Default verify_all_snippets executes commands in AGENTS.md, CONTRIBUTING.md, docs/toolkit.md, and docs/usage.md."""
        executed_targets: list[str] = []
        orig_make = TOOL_DISPATCHERS[("make",)]
        orig_tar = TOOL_DISPATCHERS[("tar",)]
        orig_py = TOOL_DISPATCHERS[("python",)]

        def spy_make(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            executed_targets.append(cmd)
            return orig_make(parts, root, cmd)

        def spy_tar(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            executed_targets.append(cmd)
            return orig_tar(parts, root, cmd)

        def spy_py(parts: list[str], root: Path, cmd: str) -> tuple[bool, str]:
            executed_targets.append(cmd)
            return orig_py(parts, root, cmd)

        TOOL_DISPATCHERS[("make",)] = spy_make
        TOOL_DISPATCHERS[("tar",)] = spy_tar
        TOOL_DISPATCHERS[("python",)] = spy_py
        try:
            errors = verify_all_snippets(root=ROOT)
            self.assertEqual([], errors)
        finally:
            TOOL_DISPATCHERS[("make",)] = orig_make
            TOOL_DISPATCHERS[("tar",)] = orig_tar
            TOOL_DISPATCHERS[("python",)] = orig_py

        # AGENTS.md has concise Makefile and run_checks.py wrappers
        self.assertIn("make check", executed_targets)
        self.assertIn("make fix", executed_targets)
        self.assertIn("python scripts/run_checks.py", executed_targets)
        # CONTRIBUTING.md has package_skills.py --output /tmp/release-verify
        self.assertTrue(
            any("package_skills.py" in cmd for cmd in executed_targets),
            "Expected CONTRIBUTING.md package_skills.py snippet to execute on default run",
        )
        # docs/toolkit.md has audit_to_sarif.py
        self.assertTrue(
            any("audit_to_sarif.py" in cmd for cmd in executed_targets),
            "Expected docs/toolkit.md snippets to execute on default run",
        )
        # docs/usage.md has tar -xf fred-skills-<VERSION>-starter.tar / complete.tar
        ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertTrue(
            any(f"fred-skills-{ver}-starter.tar" in cmd for cmd in executed_targets),
            "Expected docs/usage.md starter.tar snippet to execute on default run",
        )
        self.assertTrue(
            any(f"fred-skills-{ver}-complete.tar" in cmd for cmd in executed_targets),
            "Expected docs/usage.md complete.tar snippet to execute on default run",
        )

    def test_tar_validation_enforces_compression_and_rejects_unknown_flags(
        self,
    ) -> None:
        """_validate_tar accepts -xf/-tf on .tar and -xzf/-ztf on .tar.gz, rejecting mismatches and unknown flags."""
        ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        valid_cmds = [
            f"tar -xf fred-skills-{ver}-starter.tar -C /path/to/project/.agents/skills/",
            f"tar -xf fred-skills-{ver}-complete.tar -C /path/to/project/.agents/skills/",
            f"tar -tf fred-skills-{ver}-starter.tar",
            "tar -xzf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/",
            "tar -ztf repo-audit-concurrency.tar.gz",
        ]
        for cmd in valid_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected valid tar command to pass: {cmd} -> {msg}")

        invalid_cmds = [
            f"tar -xzf fred-skills-{ver}-starter.tar -C /path/to/project/.agents/skills/",
            f"tar -ztf fred-skills-{ver}-complete.tar",
            "tar -xf repo-audit-concurrency.tar.gz -C /path/to/project/.agents/skills/",
            "tar -tf fred-skills-suite.tar.gz",
            "tar -xzf repo-audit-concurrency.tar.gz --invalid-flag",
            f"tar -xf fred-skills-{ver}-starter.tar --unknown-option",
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
            "python scripts/check_workflow_pins.py --invalid-flag",
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
            "/plugin install fred-skills@fred-skills --invalid-flag",
            "/plugin install complete@fred-skills --invalid-flag",
            "mkdir -p /path/to/project/.agent/skills/",
            "ls -la /path/to/project/.agent/skills/repo-audit-concurrency/SKILL.md",
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
        """AGENTS.md stays concise with top-level wrappers, delegates scripts to CONTRIBUTING.md, and has @AGENTS.md host shims."""
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        agents_lines = agents_text.splitlines()
        self.assertLessEqual(
            len(agents_lines),
            30,
            f"Expected AGENTS.md to stay at 30 lines or fewer, got {len(agents_lines)}",
        )
        self.assertIn("[CONTRIBUTING.md](CONTRIBUTING.md)", agents_text)
        agents_blocks = extract_blocks_from_file(ROOT / "AGENTS.md", root=ROOT)
        self.assertEqual(1, len(agents_blocks))
        self.assertEqual(
            ["make check", "make fix", "python scripts/run_checks.py"],
            [cmd for _, cmd in agents_blocks[0].commands],
        )
        self.assertNotIn("python scripts/check_doc_links.py", agents_text)
        self.assertNotIn("python scripts/verify_doc_snippets.py", agents_text)
        self.assertNotIn("python scripts/package_skills.py", agents_text)

        contributing_text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("python scripts/check_doc_links.py", contributing_text)
        self.assertIn("python scripts/verify_doc_snippets.py", contributing_text)
        self.assertIn(
            "python scripts/package_skills.py --output /tmp/release-verify",
            contributing_text,
        )

        claude_text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual("@AGENTS.md", claude_text.strip())
        gemini_text = (ROOT / "GEMINI.md").read_text(encoding="utf-8")
        self.assertEqual("@AGENTS.md", gemini_text.strip())
        copilot_text = (ROOT / ".github/copilot-instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("[AGENTS.md](../AGENTS.md)", copilot_text)

    def test_yaml_workflow_run_steps_extracted_and_reject_invalid_flags(self) -> None:
        """YAML workflow blocks in docs/integration.md execute run steps and reject invalid flags."""
        integration_blocks = discover_doc_blocks(root=ROOT)["docs/integration.md"]
        yaml_blocks = [b for b in integration_blocks if b.language in ("yaml", "yml")]
        self.assertEqual(1, len(yaml_blocks))
        yaml_cmds = [cmd for _, cmd in yaml_blocks[0].commands]
        self.assertTrue(
            any(
                "scripts/fred.py plan ." in cmd and "--json > audit-plan.json" in cmd
                for cmd in yaml_cmds
            ),
            f"Expected plan --json redirection in YAML commands, got: {yaml_cmds}",
        )
        self.assertTrue(
            any("data['plan']" in cmd for cmd in yaml_cmds),
            f"Expected data['plan'] assertion in YAML commands, got: {yaml_cmds}",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            (tmp_root / "scripts").symlink_to(
                ROOT / "scripts", target_is_directory=True
            )
            (tmp_root / "docs").mkdir(parents=True)
            bad_yaml_doc = tmp_root / "docs/integration.md"
            bad_yaml_doc.write_text(
                "```yaml\n"
                "jobs:\n"
                "  audit:\n"
                "    steps:\n"
                "      - name: Bad plan flag\n"
                "        run: |\n"
                "          python scripts/fred.py plan . \\\n"
                "            --task review-pr \\\n"
                "            --output-plan audit-plan.json\n"
                "      - run: python scripts/fred.py search --bogus-inline-flag\n"
                "```\n",
                encoding="utf-8",
            )
            errors = verify_all_snippets(root=tmp_root, files=["docs/integration.md"])
            self.assertTrue(
                any(
                    "docs/integration.md:L7:" in err and "--output-plan" in err
                    for err in errors
                ),
                f"Expected exact-line --output-plan failure, got: {errors}",
            )
            self.assertTrue(
                any(
                    "docs/integration.md:L10:" in err and "--bogus-inline-flag" in err
                    for err in errors
                ),
                f"Expected exact-line inline run failure, got: {errors}",
            )

    def test_version_pack_and_skill_count_drift_detected_with_exact_lines(self) -> None:
        """Modifying VERSION or catalog/packs.json without updating release docs reports exact file lines."""
        # Direct curl and tar checks against wrong version or unknown/removed pack
        bad_release_cmds = [
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v9.9.9/SHA256SUMS",
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-0.1.0-starter.tar",
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/fred-skills-0.2.0-architecture.tar",
            "curl -LO https://github.com/fderuiter/fred-skills/releases/download/v0.2.0/nonexistent-skill.tar.gz",
            "tar -xf fred-skills-9.9.9-starter.tar -C /path/to/project/.agents/skills/",
            "tar -xf fred-skills-0.2.0-architecture.tar -C /path/to/project/.agents/skills/",
            "tar -xzf nonexistent-skill.tar.gz -C /path/to/project/.agents/skills/",
        ]
        for bad_cmd in bad_release_cmds:
            ok, _ = execute_snippet_command(bad_cmd, root=ROOT)
            self.assertFalse(ok, f"Expected release drift probe to fail: {bad_cmd}")

        active_ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        active_notes = f"docs/release-notes-{active_ver}.md"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            (tmp_root / "scripts").symlink_to(
                ROOT / "scripts", target_is_directory=True
            )
            (tmp_root / "skills").symlink_to(ROOT / "skills", target_is_directory=True)
            (tmp_root / "examples").symlink_to(
                ROOT / "examples", target_is_directory=True
            )
            for rel in (
                "README.md",
                "Makefile",
                "requirements-dev.txt",
                ".claude-plugin/marketplace.json",
                "catalog/hosts.json",
                "catalog/packs.json",
                "docs/usage.md",
                "docs/release-notes-0.2.0.md",
                active_notes,
            ):
                dst = tmp_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((ROOT / rel).read_bytes())

            # 1. Bump VERSION without updating README.md or docs/usage.md
            (tmp_root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
            ver_errors = verify_all_snippets(
                root=tmp_root, files=["README.md", "docs/usage.md"]
            )
            self.assertTrue(
                any("README.md:L117:" in err and "v9.9.9" in err for err in ver_errors),
                f"Expected README.md:L117 version drift failure, got: {ver_errors}",
            )
            self.assertTrue(
                any(
                    "docs/usage.md:L147:" in err and "v9.9.9" in err
                    for err in ver_errors
                ),
                f"Expected docs/usage.md:L147 version drift failure, got: {ver_errors}",
            )
            self.assertTrue(
                any(
                    "docs/usage.md:L175:" in err and "9.9.9" in err
                    for err in ver_errors
                ),
                f"Expected docs/usage.md:L175 tar version drift failure, got: {ver_errors}",
            )

            # Restore VERSION and mutate catalog/packs.json skill count without updating docs
            (tmp_root / "VERSION").write_text(f"{active_ver}\n", encoding="utf-8")
            import json

            packs_doc = json.loads(
                (tmp_root / "catalog/packs.json").read_text(encoding="utf-8")
            )
            packs_doc["packs"]["complete"]["skills"].pop()
            (tmp_root / "catalog/packs.json").write_text(
                json.dumps(packs_doc), encoding="utf-8"
            )
            count_errors = verify_all_snippets(
                root=tmp_root,
                files=[
                    "README.md",
                    "docs/usage.md",
                    "docs/release-notes-0.2.0.md",
                    active_notes,
                ],
            )
            self.assertTrue(
                any("README.md:L4:" in err and "56" in err for err in count_errors),
                f"Expected README.md:L4 count drift failure, got: {count_errors}",
            )
            self.assertTrue(
                any(
                    "docs/usage.md:L20:" in err and "56" in err for err in count_errors
                ),
                f"Expected docs/usage.md:L20 count drift failure, got: {count_errors}",
            )
            self.assertTrue(
                any(
                    f"{active_notes}:L6:" in err and "56" in err for err in count_errors
                ),
                f"Expected active release-notes L6 count drift failure, got: {count_errors}",
            )
            self.assertFalse(
                any("docs/release-notes-0.2.0.md" in err for err in count_errors),
                f"Historical release-notes-0.2.0.md must be exempt from HEAD count checks: {count_errors}",
            )

    def test_shell_prefixes_control_keywords_env_and_assignments_unwrapped(
        self,
    ) -> None:
        """Commands prefixed by if, then, for, do, env, or VAR=val fail when inner flags are invalid."""
        invalid_prefixed = [
            "if python scripts/fred.py search --invalid-flag; then echo ok; fi",
            "if [ -f audit.json ]; then python scripts/fred.py issues audit.json --invalid-flag; fi",
            "for run_dir in a b; do python scripts/fred.py validate-run $run_dir --invalid-flag; done",
            "for python scripts/fred.py search --invalid-flag",
            "do make check --invalid-flag",
            "env PYTHONDONTWRITEBYTECODE=1 python scripts/run_checks.py --invalid-flag",
            "env -i -u HOME FOO=bar ruff check . --invalid-flag",
            "PYTHONDONTWRITEBYTECODE=1 SKIP=mypy pre-commit run --all-files --invalid-flag",
        ]
        for cmd in invalid_prefixed:
            ok, _ = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Expected prefixed invalid command to fail: {cmd}")

        valid_prefixed = [
            "if [ -f audit.json ]; then python scripts/fred.py issues audit.json --output audit-drafts --format markdown; fi",
            "for tar in a b; do python scripts/fred.py installed --destination /path/to/project --host codex; done",
            "env PYTHONDONTWRITEBYTECODE=1 python scripts/run_checks.py --phase docs",
            "SKIP=mypy pre-commit run --all-files",
        ]
        for cmd in valid_prefixed:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(
                ok, f"Expected prefixed valid command to pass: {cmd} -> {msg}"
            )

    def test_fred_catalog_flags_and_gh_subcommand_flags_validated(self) -> None:
        """Invalid --skill, --profile, or --case values fail, while valid gh release and gh issue flags pass."""
        invalid_catalog_cmds = [
            "python scripts/fred.py install --destination /path/to/project --host codex --skill nonexistent-skill",
            "python scripts/fred.py remove --destination /path/to/project --host codex --skill=nonexistent-skill",
            "python scripts/fred.py plan /path/to/project --task review-pr --profile nonexistent-profile",
            'python scripts/fred.py evaluate --output eval-out --adapter-argv \'["python", "a.py"]\' --case nonexistent-case',
        ]
        for cmd in invalid_catalog_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Expected invalid catalog command to fail: {cmd}")
            self.assertTrue(msg, f"Expected non-empty failure reason for: {cmd}")

        valid_catalog_cmds = [
            "python scripts/fred.py install --destination /path/to/project --host codex --skill repo-audit-concurrency",
            "python scripts/fred.py plan /path/to/project --task review-pr --profile web-app",
            "python scripts/fred.py plan /path/to/project --task <task> --profile <profile>",
            'python scripts/fred.py evaluate --output eval-out --adapter-argv \'["python", "a.py"]\' --case concurrency-a',
            'python scripts/fred.py evaluate --output eval-out --adapter-argv \'["python", "a.py"]\' --case <case>',
        ]
        for cmd in valid_catalog_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(
                ok, f"Expected valid catalog command to pass: {cmd} -> {msg}"
            )

        valid_gh_cmds = [
            "gh release create v0.2.0 --verify-tag --target main --notes-file docs/release-notes-0.2.0.md",
            'gh release download v0.2.0 --pattern "*.tar.gz" --clobber --repo fderuiter/fred-skills',
            'gh issue create --title "Defect" --body "Details" --label bug --repo fderuiter/fred-skills',
            "gh issue list --state open --limit 10 --repo fderuiter/fred-skills",
        ]
        for cmd in valid_gh_cmds:
            ok, msg = execute_snippet_command(cmd, root=ROOT)
            self.assertTrue(ok, f"Expected valid gh command to pass: {cmd} -> {msg}")

        with patch("verify_doc_snippets.shutil.which", return_value=None):
            for cmd in valid_gh_cmds:
                ok_fb, msg_fb = execute_snippet_command(cmd, root=ROOT)
                self.assertTrue(
                    ok_fb, f"Expected fallback gh check to pass: {cmd} -> {msg_fb}"
                )

        invalid_gh_cmds = [
            "gh release create v0.2.0 --deny-self-hosted-runners",
            "gh issue create --predicate-type https://slsa.dev/provenance/v1",
            "gh release view v0.2.0 --invalid-flag",
            "gh issue list --invalid-flag",
        ]
        for cmd in invalid_gh_cmds:
            ok, _ = execute_snippet_command(cmd, root=ROOT)
            self.assertFalse(ok, f"Expected invalid gh subcommand flag to fail: {cmd}")

    def test_python_yaml_and_audit_json_block_verification(self) -> None:
        """Python syntax errors, invalid JSON, and broken Audit Schema 1.0 JSON blocks fail with exact line numbers."""
        import json

        from fredlib import runs

        integration_blocks = discover_doc_blocks(root=ROOT)["docs/integration.md"]
        json_blocks = [b for b in integration_blocks if b.language == "json"]
        self.assertEqual(1, len(json_blocks))
        parsed_audit = json.loads(json_blocks[0].content)
        self.assertEqual([], runs.audit_errors(parsed_audit))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            doc_path = tmp_root / "docs/broken_blocks.md"
            doc_path.parent.mkdir(parents=True)
            doc_path.write_text(
                "```python\n"
                "def bad_func(x: int\n"
                "    return x\n"
                "```\n\n"
                "```json\n"
                "{\n"
                '  "recovery": "already-committed",\n'
                '  "skills": [...]\n'
                "}\n"
                "```\n\n"
                "```json\n"
                "{\n"
                '  "schema_version": "1.0",\n'
                '  "findings": [\n'
                '    {"id": "F-001", "status": "candidate"}\n'
                "  ]\n"
                "}\n"
                "```\n",
                encoding="utf-8",
            )
            errs = verify_all_snippets(root=tmp_root, files=["docs/broken_blocks.md"])
            self.assertTrue(
                any(
                    "docs/broken_blocks.md:L2:" in e and "Python syntax error" in e
                    for e in errs
                ),
                f"Expected Python syntax error at L2, got: {errs}",
            )
            self.assertTrue(
                any(
                    "docs/broken_blocks.md:L9:" in e and "Invalid JSON syntax" in e
                    for e in errs
                ),
                f"Expected JSON syntax error at L9, got: {errs}",
            )
            self.assertTrue(
                any(
                    "docs/broken_blocks.md:L14:" in e
                    and "Audit schema validation failed" in e
                    for e in errs
                ),
                f"Expected Audit schema error at L14, got: {errs}",
            )

    def test_cursor_heading_sections_reject_claude_plugin_slash_commands(self) -> None:
        """Fenced blocks inside Cursor headings must reject /plugin commands while sibling Claude Code sections allow them."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            for rel in (
                ".claude-plugin/marketplace.json",
                ".cursor-plugin/marketplace.json",
            ):
                dst = tmp_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes((ROOT / rel).read_bytes())

            doc_path = tmp_root / "docs/cursor_heading_check.md"
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_text(
                "## Installation\n\n"
                "### Claude Code\n\n"
                "```sh\n"
                "/plugin marketplace add fderuiter/fred-skills\n"
                "/plugin install fred-skills@fred-skills\n"
                "```\n\n"
                "### Cursor\n\n"
                "#### Marketplace Import\n\n"
                "```sh\n"
                "/plugin install fred-skills@fred-skills\n"
                "```\n\n"
                "### Claude Code and Cursor\n\n"
                "```text\n"
                "/plugin marketplace add fderuiter/fred-skills\n"
                "```\n\n"
                "### Codex\n\n"
                "```text\n"
                "$skill-installer install https://github.com/fderuiter/fred-skills/tree/main/skills/repo-audit-concurrency\n"
                "```\n",
                encoding="utf-8",
            )

            blocks = extract_blocks_from_file(doc_path, root=tmp_root)
            self.assertEqual(4, len(blocks))
            self.assertEqual(("Installation", "Claude Code"), blocks[0].headings)
            self.assertEqual(
                ("Installation", "Cursor", "Marketplace Import"),
                blocks[1].headings,
            )
            self.assertEqual(
                ("Installation", "Claude Code and Cursor"),
                blocks[2].headings,
            )
            self.assertEqual(("Installation", "Codex"), blocks[3].headings)

            errs = verify_all_snippets(
                root=tmp_root, files=["docs/cursor_heading_check.md"]
            )
            self.assertEqual(2, len(errs), f"Expected 2 Cursor heading errors: {errs}")
            self.assertTrue(
                any("L15:" in e and "'Cursor'" in e for e in errs),
                errs,
            )
            self.assertTrue(
                any("L21:" in e and "'Claude Code and Cursor'" in e for e in errs),
                errs,
            )

    def test_runbook_symbol_citations_resolve_and_forbid_raw_lines(self) -> None:
        """docs/runbooks.md uses zero raw line citations and 100% AST-valid symbol citations."""
        self.assertEqual([], verify_runbook_citations(root=ROOT))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            runbooks_dst = tmp_root / "docs/runbooks.md"
            runbooks_dst.parent.mkdir(parents=True, exist_ok=True)

            # 1. Raw :L<line> and bare L<start>-L<end> citations must fail with exact line numbers
            runbooks_dst.write_text(
                "# Runbooks\n\n"
                "Valid ref: `fredlib.installation.lock`.\n"
                "Forbidden colon line: `scripts/fredlib/installation.py:L113`.\n"
                "Forbidden range: `L102-L135`.\n",
                encoding="utf-8",
            )
            line_errs = verify_all_snippets(root=tmp_root, files=["docs/runbooks.md"])
            self.assertEqual(
                2, len(line_errs), f"Expected 2 line citation errors: {line_errs}"
            )
            self.assertTrue(
                any("docs/runbooks.md:L4:" in e and ":L113" in e for e in line_errs),
                line_errs,
            )
            self.assertTrue(
                any(
                    "docs/runbooks.md:L5:" in e and "L102-L135" in e for e in line_errs
                ),
                line_errs,
            )

            # 2. Missing AST symbols and unknown submodules must fail with exact line numbers
            runbooks_dst.write_text(
                "# Runbooks\n\n"
                "Missing dotted symbol: `fredlib.installation.nonexistent_helper`.\n"
                "Unknown module: `fredlib.nonexistent_mod.some_fn`.\n"
                "Missing colon symbol: `installation.py:missing_symbol`.\n",
                encoding="utf-8",
            )
            sym_errs = verify_all_snippets(root=tmp_root, files=["docs/runbooks.md"])
            self.assertEqual(
                3, len(sym_errs), f"Expected 3 symbol citation errors: {sym_errs}"
            )
            self.assertTrue(
                any(
                    "docs/runbooks.md:L3:" in e and "nonexistent_helper" in e
                    for e in sym_errs
                ),
                sym_errs,
            )
            self.assertTrue(
                any(
                    "docs/runbooks.md:L4:" in e and "fredlib.nonexistent_mod" in e
                    for e in sym_errs
                ),
                sym_errs,
            )
            self.assertTrue(
                any(
                    "docs/runbooks.md:L5:" in e and "missing_symbol" in e
                    for e in sym_errs
                ),
                sym_errs,
            )

            # 3. Refactoring a Python module in root to rename a cited symbol triggers AST failure
            custom_runs = tmp_root / "scripts/fredlib/runs.py"
            custom_runs.parent.mkdir(parents=True, exist_ok=True)
            custom_runs.write_text(
                "def renamed_validate_run():\n    return []\n", encoding="utf-8"
            )
            runbooks_dst.write_text(
                "# Runbooks\n\nCheck `fredlib.runs.validate_run` before advancing.\n",
                encoding="utf-8",
            )
            drift_errs = verify_runbook_citations(root=tmp_root)
            self.assertEqual(1, len(drift_errs), drift_errs)
            self.assertIn("docs/runbooks.md:L3:", drift_errs[0])
            self.assertIn("validate_run", drift_errs[0])

    def test_cached_subprocess_timeout_not_cached_and_main_exits_cleanly(self) -> None:
        """_run_cached_subprocess sets timeout=60 without caching timeouts, and main exits 1 cleanly."""
        import contextlib
        import io

        import verify_doc_snippets
        from skill_tools import FredCliError

        clear_subprocess_cache()
        calls: list[float | None] = []

        def flaky_run(
            cmd: list[str], *_args: object, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            timeout_val = kwargs.get("timeout")
            calls.append(timeout_val if isinstance(timeout_val, (int, float)) else None)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="ok\n", stderr=""
            )

        try:
            with patch("verify_doc_snippets.subprocess.run", side_effect=flaky_run):
                with self.assertRaises(subprocess.TimeoutExpired):
                    verify_doc_snippets._run_cached_subprocess(
                        ["ruff", "check", "."], cwd=ROOT
                    )
                # Timeout must not be cached; second invocation succeeds and caches result
                recovered = verify_doc_snippets._run_cached_subprocess(
                    ["ruff", "check", "."], cwd=ROOT
                )
                cached = verify_doc_snippets._run_cached_subprocess(
                    ["ruff", "check", "."], cwd=ROOT
                )
            self.assertEqual([60, 60], calls)
            self.assertEqual(0, recovered.returncode)
            self.assertIs(recovered, cached)
        finally:
            clear_subprocess_cache()

        err_sub = io.StringIO()
        with (
            patch(
                "verify_doc_snippets.verify_all_snippets",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["ruff", "check", "."], timeout=60
                ),
            ),
            contextlib.redirect_stderr(err_sub),
        ):
            rc_sub = verify_doc_snippets.main(["README.md"])
        self.assertEqual(1, rc_sub)
        self.assertIn("Error [subprocess_timeout]:", err_sub.getvalue())
        self.assertNotIn("Traceback", err_sub.getvalue())

        err_git = io.StringIO()
        with (
            patch(
                "verify_doc_snippets.verify_all_snippets",
                side_effect=FredCliError(
                    "Git timed out",
                    code="git_timeout",
                    hint="Retry after clearing .git/index.lock.",
                ),
            ),
            contextlib.redirect_stderr(err_git),
        ):
            rc_git = verify_doc_snippets.main(["README.md"])
        self.assertEqual(1, rc_git)
        self.assertIn("Error [git_timeout]:", err_git.getvalue())
        self.assertIn("Hint:", err_git.getvalue())


if __name__ == "__main__":
    unittest.main()
