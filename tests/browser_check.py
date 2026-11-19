"""Exercise generated local pages in a real browser, including hostile report text."""

import argparse
import functools
import http.server
import shutil
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
from build_site import build_site
from fredlib.common import read_json
from fredlib.reports import render
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--screenshot", type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory() as temporary:
    report = Path(temporary) / "report.html"
    audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
    audit["findings"][0]["title"] = (
        '<img src="https://example.invalid/x" onerror="window.injected=true">'
    )
    render(audit, report)
    with sync_playwright() as playwright:
        # An installed system Chromium is useful in managed environments without
        # Playwright downloads; ordinary CI uses its version-matched browser.
        system_browser = shutil.which("chromium") or shutil.which("chromium-browser")
        browser = playwright.chromium.launch(
            headless=True, executable_path=system_browser
        )
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        network = []
        page.on(
            "request",
            lambda request: (
                network.append(request.url)
                if request.url.startswith(("http:", "https:"))
                else None
            ),
        )
        page.goto(report.as_uri())
        assert page.locator("article.finding").count() == 1
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        page.get_by_label("Evidence status").select_option("refuted")
        assert page.locator("article.finding:visible").count() == 0
        page.get_by_label("Evidence status").select_option("")
        page.get_by_label("Search findings").fill("old result")
        assert page.locator("article.finding:visible").count() == 0
        page.get_by_label("Search findings").fill("")
        assert page.locator("article.finding:visible").count() == 1
        page.get_by_text(
            "Developer: failure, source, and correction", exact=True
        ).click()
        assert page.get_by_text("No publication fence", exact=False).count() > 0
        assert not network
        page.screenshot(path="/tmp/audit_report_with_findings.png")
        empty_report = Path(temporary) / "empty_report.html"
        empty_audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        empty_audit["findings"] = []
        render(empty_audit, empty_report)
        page_errors = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.goto(empty_report.as_uri())
        assert not page_errors
        assert page.locator("article.finding").count() == 0
        assert page.locator("#query").count() == 0
        assert page.locator("#severity").count() == 0
        assert page.locator("#status").count() == 0
        assert page.locator("#remediation").count() == 0
        assert page.locator("#empty-state").count() == 0
        assert page.locator("#reset-filters").count() == 0
        assert (
            page.get_by_text(
                "No findings recorded. Review coverage before drawing conclusions.",
                exact=True,
            ).count()
            == 1
        )
        page.screenshot(path="/tmp/empty_audit_report.png")

        triage_report = Path(temporary) / "triage_comparison_report.html"
        prior_audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        prior_audit["run_id"] = "audit-2026-11-01"
        curr_audit = read_json(ROOT / "tests/fixtures/audit-valid.json")
        curr_audit["run_id"] = "audit-2026-11-18"
        curr_audit["limitations"] = []
        curr_audit["evidence"].append(
            {
                "id": "E-2",
                "path": "queue.mjs",
                "line_start": 12,
                "line_end": 19,
                "symbol": "Queue.drain",
                "context": "Unbounded drain batch",
            }
        )
        curr_audit["findings"][0].update(
            severity="critical",
            status="confirmed",
            validation={
                "state": "executed",
                "commands": ["node examples/walkthrough/check.mjs"],
                "result": "Original race reproduced; fenced variant passed",
            },
            related_findings=["F-2"],
        )
        curr_audit["findings"].append(
            {
                **curr_audit["findings"][0],
                "id": "F-2",
                "topic": "resilience",
                "title": "Unbounded drain batch under burst traffic",
                "severity": "high",
                "status": "needs-validation",
                "contract_ids": [],
                "evidence_ids": ["E-2"],
                "root_cause": "Missing batch limit",
                "validation": {
                    "state": "not-run",
                    "commands": [],
                    "result": "Needs load harness",
                },
                "related_findings": [],
            }
        )
        remediation_doc = {
            "schema_version": "1.0",
            "records": [
                {
                    "at": "2026-11-18T16:00:00Z",
                    "finding_id": "F-1",
                    "state": "verified-fixed",
                    "note": "Verified fenced publication and safe controls.",
                }
            ],
        }
        render(
            curr_audit, triage_report, prior=prior_audit, remediation=remediation_doc
        )
        page.goto(triage_report.as_uri())
        assert not page_errors
        assert page.locator("#comparison pre").count() == 0
        assert page.locator("#comparison table").count() == 5
        assert page.locator('#comparison a[href="#F-1"]').count() >= 1
        assert page.locator('#comparison a[href="#F-2"]').count() >= 1
        assert page.locator("pre:empty").count() == 0
        assert page.locator("ul:empty").count() == 0
        assert (
            page.locator("article#F-1")
            .get_by_text("Verified fixed", exact=True)
            .count()
            == 1
        )
        assert (
            page.locator("article#F-1")
            .get_by_text("Verified fenced publication and safe controls.")
            .count()
            == 1
        )
        assert page.locator("#count").text_content() == "2 findings shown"

        # Combine severity, status, and remediation filters
        page.get_by_label("Severity").select_option("critical")
        assert page.locator("article.finding:visible").count() == 1
        assert page.locator("#count").text_content() == "1 finding shown"
        page.get_by_label("Remediation state").select_option("unreconciled")
        assert page.locator("article.finding:visible").count() == 0
        assert page.locator("#empty-state").is_visible()
        page.get_by_role("button", name="Reset filters").click()
        assert page.locator("article.finding:visible").count() == 2
        assert not page.locator("#empty-state").is_visible()
        page.screenshot(path="/tmp/triage_filters_and_badges.png")
        page.locator('#comparison a[href="#F-2"]').first.click()
        assert page.url.endswith("#F-2")
        page.locator("#comparison").scroll_into_view_if_needed()
        page.screenshot(path="/tmp/comparison_tables.png")

        arch_report = Path(temporary) / "architecture_report.html"
        render(
            {
                "schema_version": "1.0",
                "title": "Order Fulfillment Architecture",
                "status": "accepted",
                "boundaries": [
                    {
                        "name": "Ingress API Gateway",
                        "type": "trust",
                        "description": "Terminates external TLS and validates session tokens",
                        "invariants": [
                            "Authenticated principal required before downstream dispatch",
                            '<img src="https://example.invalid/arch" onerror="window.injected=true">',
                        ],
                    },
                    {
                        "name": "Ledger Store",
                        "type": "storage",
                        "description": "Append-only settlement journal",
                        "invariants": ["Idempotent write keys enforced per tenant"],
                    },
                ],
                "decisions": [
                    {
                        "id": "ADR-001",
                        "title": "Adopt transactional outbox for settlement events",
                        "context": "Dual writes risked lost downstream notifications on crash",
                        "decision": "Persist outbox entries in the same storage transaction",
                        "consequences": [
                            "At-least-once event delivery requires idempotent consumers"
                        ],
                    }
                ],
            },
            arch_report,
        )
        page.goto(arch_report.as_uri())
        assert not page_errors
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        assert page.locator("article.finding").count() == 1
        page.get_by_label("Search decisions").fill("nonexistent")
        assert page.locator("article.finding:visible").count() == 0
        page.get_by_role("button", name="Reset filters").click()
        assert page.locator("article.finding:visible").count() == 1
        page.get_by_text("Consequences and trade-offs", exact=True).click()
        page.screenshot(path="/tmp/architecture_report.png")

        review_report = Path(temporary) / "review_report.html"
        render(
            {
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "Differential review identified a missing lock guard and an advisory cleanup.",
                "findings": [
                    {
                        "id": "REV-01",
                        "title": "Missing lock around shared token cache refresh",
                        "severity": "high",
                        "file": "src/auth/cache.py",
                        "line": 84,
                        "description": 'Concurrent callers can overwrite fresh credentials <img src="https://example.invalid/rev" onerror="window.injected=true">',
                        "recommendation": "Acquire the refresh mutex before updating cache entries.",
                    },
                    {
                        "id": "REV-02",
                        "title": "Document retry backoff ceiling constant",
                        "severity": "advisory",
                        "file": "src/auth/client.py",
                        "line": 19,
                        "description": "Timeout ceiling unit is milliseconds rather than seconds.",
                        "recommendation": "Rename constant with _MS suffix for clarity.",
                    },
                ],
            },
            review_report,
        )
        page.goto(review_report.as_uri())
        assert not page_errors
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        assert page.locator("article.finding").count() == 2
        page.get_by_label("Severity").select_option("advisory")
        assert page.locator("article.finding:visible").count() == 1
        page.get_by_label("Severity").select_option("")
        assert page.locator("article.finding:visible").count() == 2
        page.screenshot(path="/tmp/review_report.png")

        testing_report = Path(temporary) / "testing_report.html"
        render(
            {
                "schema_version": "1.0",
                "component": "settlement.outbox",
                "framework": "pytest",
                "test_cases": [
                    {
                        "id": "TC-01",
                        "name": "test_duplicate_idempotency_key_preserves_single_entry",
                        "description": "Replaying an identical settlement request commits exactly one journal row.",
                        "oracle_type": "state",
                        "verified": True,
                    },
                    {
                        "id": "TC-02",
                        "name": "test_zero_byte_payload_raises_validation_error",
                        "description": 'Empty batch boundary raises ValueError before opening a transaction <img src="https://example.invalid/tst" onerror="window.injected=true">',
                        "oracle_type": "exception",
                        "verified": True,
                    },
                ],
            },
            testing_report,
        )
        page.goto(testing_report.as_uri())
        assert not page_errors
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        assert page.locator("article.finding").count() == 2
        page.get_by_label("Oracle type").select_option("exception")
        assert page.locator("article.finding:visible").count() == 1
        page.get_by_label("Oracle type").select_option("")
        assert page.locator("article.finding:visible").count() == 2
        page.screenshot(path="/tmp/testing_report.png")

        expected_packs = read_json(ROOT / "catalog/packs.json")["packs"]
        expected_hosts = read_json(ROOT / "catalog/hosts.json")["hosts"]
        page.goto((ROOT / "docs/explorer.html").as_uri())
        count = page.locator("article.finding").count()
        assert count >= 41
        assert page.locator("article.pack-card").count() == len(expected_packs) == 11
        assert page.locator("#hosts tbody tr").count() == len(expected_hosts)
        assert page.locator("#hosts tbody tr").count() >= 4
        page.get_by_label("Task or symptom").fill("stale response")
        assert 0 < page.locator("article.finding:visible").count() < count
        page.get_by_label("Task or symptom").fill("")
        page.get_by_label("Family").select_option("repository-workflows")
        assert page.locator("article.finding:visible").count() == 6
        target_card = page.locator("article#repo-audit-concurrency")
        assert not target_card.is_visible()
        page.get_by_label("Family").select_option("")
        assert page.locator("article.finding:visible").count() == count
        assert target_card.is_visible()
        page.get_by_text(
            "review-pr — Review changed behavior and affected contracts.",
            exact=True,
        ).click()
        skill_link = page.locator('a[href="#repo-audit-concurrency"]').first
        assert skill_link.is_visible()
        skill_link.click()
        assert target_card.is_visible()
        assert page.url.endswith("#repo-audit-concurrency")
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot))
        assert not network

        web_root = Path(temporary) / "web_root"
        mounted_site = web_root / "fred-skills"
        build_site(mounted_site, root=ROOT)

        class _QuietStaticHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format: str, *log_args: object) -> None:
                return

        handler_cls = functools.partial(_QuietStaticHandler, directory=str(web_root))
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        server_port = httpd.server_address[1]
        server_origin = f"http://127.0.0.1:{server_port}"
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        try:
            web_page = browser.new_page(viewport={"width": 1100, "height": 850})
            web_errors: list[str] = []
            external_requests: list[str] = []
            web_page.on("pageerror", lambda err: web_errors.append(str(err)))
            web_page.on(
                "request",
                lambda req: (
                    external_requests.append(req.url)
                    if req.url.startswith(("http:", "https:"))
                    and not req.url.startswith(server_origin)
                    else None
                ),
            )

            root_resp = web_page.goto(f"{server_origin}/fred-skills/")
            assert root_resp is not None and root_resp.status == 200
            assert "text/html" in (root_resp.headers.get("content-type") or "")
            assert web_page.locator("header nav a").count() >= 5
            assert web_page.locator("h1").first.inner_text() == "fred-skills"
            web_page.screenshot(path="/tmp/site_index.png")

            web_page.locator('header nav a[href="docs/explorer.html"]').click()
            web_page.wait_for_url(f"{server_origin}/fred-skills/docs/explorer.html")
            assert (
                web_page.locator("article.pack-card").count()
                == len(expected_packs)
                == 11
            )
            packs_text = web_page.locator("section#packs").inner_text()
            for pack_name in expected_packs:
                assert f"--pack {pack_name}" in packs_text
            assert web_page.locator("#hosts tbody tr").count() == len(expected_hosts)
            hosts_text = web_page.locator("section#hosts").inner_text()
            for host_name, host_cfg in expected_hosts.items():
                assert host_name in hosts_text
                assert host_cfg["project_path"] in hosts_text
                assert "not-executed" in host_cfg["behavior_test"]
            assert "not-executed" in hosts_text
            web_page.screenshot(path="/tmp/site_explorer_packs_hosts.png")

            explorer_base = f"{server_origin}/fred-skills/docs/explorer.html"
            file_hrefs: list[str] = web_page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href'))
                    .filter(h => h && !h.startsWith('#') && !h.startsWith('http:') && !h.startsWith('https:'))"""
            )
            assert len(file_hrefs) >= 54
            for href in file_hrefs:
                assert not href.split("#")[0].endswith(".md"), href
                target_url = urllib.parse.urljoin(explorer_base, href)
                assert target_url.startswith(f"{server_origin}/fred-skills/"), (
                    target_url
                )
                with urllib.request.urlopen(target_url, timeout=5) as http_resp:
                    assert http_resp.status == 200, target_url
                    content_type = http_resp.headers.get("Content-Type", "")
                    assert "text/html" in content_type, (target_url, content_type)

            web_page.locator("header nav a", has_text="Task guide").click()
            web_page.wait_for_url(f"{server_origin}/fred-skills/docs/experience.html")
            assert "Choose a task or pack" in web_page.locator("h1").first.inner_text()

            web_page.locator("header nav a", has_text="Catalog").click()
            web_page.wait_for_url(f"{server_origin}/fred-skills/docs/catalog.html")
            assert (
                web_page.locator('table a[href$="/SKILL.html"]').count() == count >= 51
            )

            web_page.locator("header nav a", has_text="Skill explorer").click()
            web_page.wait_for_url(f"{server_origin}/fred-skills/docs/explorer.html")

            skill_h2_link = web_page.locator("article#repo-audit-concurrency h2 a")
            assert skill_h2_link.is_visible()
            skill_h2_link.click()
            web_page.wait_for_url(
                f"{server_origin}/fred-skills/skills/repo-audit-concurrency/SKILL.html"
            )
            meta_text = web_page.locator("section.skill-metadata").inner_text()
            assert "repo-audit-concurrency" in meta_text
            assert "repository-audits" in meta_text
            body_text = web_page.locator("body").inner_text()
            assert "---" not in body_text
            web_page.screenshot(path="/tmp/site_skill_page.png")

            assert not web_errors
            assert not external_requests
        finally:
            httpd.shutdown()
            httpd.server_close()
        browser.close()
print(
    "PASS: report/explorer filtering, safe text, no external requests, and workflow discovery"
)
