"""Tests for the zero-dependency static HTML site compiler, explorer packs/hosts, and HTTP serving."""

from __future__ import annotations

import functools
import http.server
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site
from check_doc_links import check_docs, check_file_links
from fredlib.common import read_json
from fredlib.reports import SCRIPT, STYLE, _csp_value
from skill_tools import extract_relative_links


class BuildSiteTests(unittest.TestCase):
    """Verify static HTML compilation, frontmatter stripping, link rewriting, and /fred-skills/ HTTP serving."""

    site_tmp: tempfile.TemporaryDirectory[str]
    web_root: Path
    site_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.site_tmp = tempfile.TemporaryDirectory()
        cls.web_root = Path(cls.site_tmp.name) / "web_root"
        cls.site_dir = cls.web_root / "fred-skills"
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/build_site.py"),
                "--output",
                str(cls.site_dir),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        cls.site_tmp.cleanup()

    def test_compiled_site_contains_index_readme_guides_explorer_and_51_skills(
        self,
    ) -> None:
        """build_site.py compiles index.html, README.html, docs/explorer.html, 14 guides, and 51 skills."""
        self.assertTrue((self.site_dir / ".nojekyll").is_file())
        self.assertTrue((self.site_dir / "index.html").is_file())
        self.assertTrue((self.site_dir / "README.html").is_file())
        self.assertTrue((self.site_dir / "docs/explorer.html").is_file())

        index_html = (self.site_dir / "index.html").read_text(encoding="utf-8")
        readme_html = (self.site_dir / "README.html").read_text(encoding="utf-8")
        self.assertEqual(index_html, readme_html)
        self.assertIn('href="docs/explorer.html"', index_html)
        self.assertIn('href="docs/experience.html"', index_html)
        self.assertIn('href="docs/catalog.html"', index_html)

        source_guides = sorted((ROOT / "docs").glob("*.md"))
        non_release_guides = [
            g for g in source_guides if not g.name.startswith("release-notes-")
        ]
        self.assertEqual(13, len(non_release_guides))
        self.assertGreaterEqual(len(source_guides), 15)
        for guide_md in source_guides:
            guide_html_path = self.site_dir / "docs" / f"{guide_md.stem}.html"
            self.assertTrue(guide_html_path.is_file(), guide_md.name)
            guide_html = guide_html_path.read_text(encoding="utf-8")
            for target in extract_relative_links(guide_html):
                self.assertFalse(
                    target.split("#")[0].split("?")[0].lower().endswith(".md"),
                    f"{guide_html_path.name} retained .md link: {target}",
                )

        skill_dirs = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(57, len(skill_dirs))
        for skill_name in skill_dirs:
            skill_html_path = self.site_dir / "skills" / skill_name / "SKILL.html"
            self.assertTrue(skill_html_path.is_file(), skill_name)

    def test_compiled_skill_pages_strip_frontmatter_and_render_metadata(self) -> None:
        """All 57 compiled skill pages expose structured metadata and zero raw YAML frontmatter blocks."""
        for skill_html_path in sorted((self.site_dir / "skills").glob("*/SKILL.html")):
            skill_name = skill_html_path.parent.name
            html_text = skill_html_path.read_text(encoding="utf-8")
            self.assertIn('<section class="skill-metadata"', html_text, skill_name)
            self.assertIn(f"<code>{skill_name}</code>", html_text, skill_name)
            self.assertIn(f"--skill {skill_name}", html_text, skill_name)
            stripped_tags = re.sub(
                r"(?s)<(pre|code|script|style)\b.*?</\1>", "", html_text
            )
            self.assertNotIn("---", stripped_tags, skill_name)

    def test_csp_style_and_script_reused_across_compiled_pages(self) -> None:
        """Compiled pages reuse STYLE, SCRIPT, and SHA-256 CSP headers with default-src/connect-src 'none'."""
        expected_csp = escape(_csp_value(), quote=True)
        self.assertIn("default-src &#x27;none&#x27;", expected_csp)
        self.assertIn("connect-src &#x27;none&#x27;", expected_csp)

        sample_pages = [
            self.site_dir / "index.html",
            self.site_dir / "docs/explorer.html",
            self.site_dir / "docs/catalog.html",
            self.site_dir / "docs/api.html",
            self.site_dir / "skills/repo-audit-concurrency/SKILL.html",
        ]
        for page_path in sample_pages:
            content = page_path.read_text(encoding="utf-8")
            self.assertIn(expected_csp, content, page_path.as_posix())
            self.assertIn(f"<style>{STYLE}</style>", content, page_path.as_posix())
            self.assertIn(f"<script>{SCRIPT}</script>", content, page_path.as_posix())

    def test_heading_slugs_match_api_guide_anchors_and_deduplicate(self) -> None:
        """Heading IDs match test_api_guide.py slugify rules so all TOC anchors resolve."""
        api_md = (ROOT / "docs/api.md").read_text(encoding="utf-8")
        api_html = (self.site_dir / "docs/api.html").read_text(encoding="utf-8")
        toc_anchors = re.findall(r"\[.+?\]\(#([a-z0-9_-]+)\)", api_md)
        self.assertGreater(len(toc_anchors), 0)
        for anchor in toc_anchors:
            self.assertIn(f'id="{anchor}"', api_html, anchor)

        _, dup_html = build_site.compile_markdown(
            "# Overview\n\n## Overview\n\n### `import_sarif`\n",
            source_rel_path="docs/sample.md",
            compiled_md_paths=frozenset(),
            root=ROOT,
        )
        self.assertIn('<h1 id="overview">Overview</h1>', dup_html)
        self.assertIn('<h2 id="overview-1">Overview</h2>', dup_html)
        self.assertIn('<h3 id="import_sarif"><code>import_sarif</code></h3>', dup_html)

    def test_markdown_compiler_escapes_html_and_handles_code_pipes_in_tables(
        self,
    ) -> None:
        """Compiler escapes hostile HTML tags and preserves pipes inside inline code in table cells."""
        md = (
            "# Safe <script>alert(1)</script>\n\n"
            "Check <host> and [Guide](docs/toolkit.md#discover).\n\n"
            "| Param | Type |\n"
            "| --- | --- |\n"
            '| `host` | `str | None` <img src="https://evil.invalid/x" onerror="1"> |\n'
        )
        _, html = build_site.compile_markdown(
            md,
            source_rel_path="README.md",
            compiled_md_paths=frozenset({"docs/toolkit.md"}),
            root=ROOT,
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img ", html)
        self.assertIn("&lt;host&gt;", html)
        self.assertIn('<a href="docs/toolkit.html#discover">Guide</a>', html)
        self.assertIn("<td><code>str | None</code>", html)

    def test_offline_and_web_explorer_render_packs_hosts_and_mode_specific_links(
        self,
    ) -> None:
        """Offline explorer uses .md links, web explorer uses .html links, and both render all packs and hosts."""
        packs = read_json(ROOT / "catalog/packs.json")["packs"]
        hosts = read_json(ROOT / "catalog/hosts.json")["hosts"]
        self.assertEqual(13, len(packs))
        self.assertGreaterEqual(len(hosts), 4)

        offline_explorer = (ROOT / "docs/explorer.html").read_text(encoding="utf-8")
        web_explorer = (self.site_dir / "docs/explorer.html").read_text(
            encoding="utf-8"
        )

        for label, html_text in (
            ("offline", offline_explorer),
            ("web", web_explorer),
        ):
            for pack_name, pack_data in packs.items():
                self.assertIn(f'id="pack-{pack_name}"', html_text, label)
                self.assertIn(f"--pack {pack_name}", html_text, label)
                self.assertIn(escape(pack_data["description"]), html_text, label)
            for host_name, host_data in hosts.items():
                self.assertIn(f'data-host="{host_name}"', html_text, label)
                self.assertIn(escape(host_data["project_path"]), html_text, label)
                self.assertIn(escape(host_data["user_path"]), html_text, label)
                self.assertIn(escape(host_data["behavior_test"]), html_text, label)

        offline_links = extract_relative_links(offline_explorer)
        self.assertGreaterEqual(len(offline_links), 55)
        self.assertTrue(all(lnk.endswith(".md") for lnk in offline_links))

        web_links = extract_relative_links(web_explorer)
        self.assertGreaterEqual(len(web_links), 55)
        self.assertTrue(all(lnk.endswith(".html") for lnk in web_links))

    def test_serving_under_fred_skills_returns_http_200_text_html_for_all_routes(
        self,
    ) -> None:
        """Serving dist/site/ under /fred-skills/ returns HTTP 200 text/html for root and all explorer links."""

        class _QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

        handler = functools.partial(_QuietHandler, directory=str(self.web_root))
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        base_origin = f"http://127.0.0.1:{port}"
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for route in (
                "/fred-skills/",
                "/fred-skills/index.html",
                "/fred-skills/README.html",
                "/fred-skills/docs/explorer.html",
            ):
                with urllib.request.urlopen(f"{base_origin}{route}", timeout=5) as resp:
                    self.assertEqual(200, resp.status, route)
                    self.assertIn("text/html", resp.headers.get("Content-Type", ""))

            explorer_url = f"{base_origin}/fred-skills/docs/explorer.html"
            web_explorer = (self.site_dir / "docs/explorer.html").read_text(
                encoding="utf-8"
            )
            links = extract_relative_links(web_explorer)
            self.assertGreaterEqual(len(links), 55)
            for href in links:
                resolved_url = urllib.parse.urljoin(explorer_url, href)
                with urllib.request.urlopen(resolved_url, timeout=5) as resp:
                    self.assertEqual(200, resp.status, resolved_url)
                    self.assertIn(
                        "text/html",
                        resp.headers.get("Content-Type", ""),
                        resolved_url,
                    )
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_check_doc_links_validates_staged_site_and_detects_broken_html_links(
        self,
    ) -> None:
        """check_docs validates docs/explorer.html and directory targets, catching broken HTML and directory links."""
        self.assertEqual(
            [],
            check_docs(root=ROOT, target_files=[ROOT / "docs/explorer.html"]),
        )
        self.assertEqual(
            [],
            check_docs(root=ROOT, target_files=[self.site_dir]),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "empty_dir").mkdir()
            bad_html = tmp_path / "page.html"
            bad_html.write_text(
                '<a href="missing.html">Missing</a><a href="empty_dir/">Dir</a>',
                encoding="utf-8",
            )
            file_errs = check_file_links(bad_html, root=tmp_path)
            self.assertEqual(2, len(file_errs), file_errs)
            self.assertTrue(any("missing.html" in e for e in file_errs))
            self.assertTrue(any("missing index.html" in e for e in file_errs))

            dir_errs = check_docs(root=tmp_path, target_files=[tmp_path])
            self.assertEqual(2, len(dir_errs))


if __name__ == "__main__":
    unittest.main()
