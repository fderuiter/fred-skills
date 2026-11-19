"""Exercise generated local pages in a real browser, including hostile report text."""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
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
        assert page.locator("#status").count() == 0
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
                "components": [
                    {
                        "name": "SettlementCoordinator",
                        "responsibility": 'Commits ledger entries and outbox messages atomically <img src="https://example.invalid/comp" onerror="window.injected=true">',
                        "owned_models": ["LedgerEntry", "OutboxEvent"],
                        "dependencies": ["Ledger Store"],
                    }
                ],
                "interfaces": [
                    {
                        "name": "POST /v1/settlements",
                        "protocol": "HTTP/JSON",
                        "producer": "SettlementCoordinator",
                        "consumers": ["BillingAPI"],
                        "contract": "Idempotent settlement submission keyed by Idempotency-Key header",
                        "compatibility": "Additive response fields only",
                    }
                ],
            },
            arch_report,
        )
        page.goto(arch_report.as_uri())
        assert not page_errors
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        assert page.locator("section#components").is_visible()
        assert page.locator("section#interfaces").is_visible()
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
                        "priority": "critical",
                        "verified": True,
                    },
                    {
                        "id": "TC-02",
                        "name": "test_zero_byte_payload_raises_validation_error",
                        "description": 'Empty batch boundary raises ValueError before opening a transaction <img src="https://example.invalid/tst" onerror="window.injected=true">',
                        "oracle_type": "exception",
                        "priority": "high",
                        "verified": True,
                    },
                ],
                "execution_summary": {
                    "state": "passed",
                    "total": 2,
                    "passed": 2,
                    "failed": 0,
                    "skipped": 0,
                    "commands": ["pytest tests/test_settlement.py"],
                    "notes": 'Verified on clean fixture <img src="https://example.invalid/exec" onerror="window.injected=true">',
                },
            },
            testing_report,
        )
        page.goto(testing_report.as_uri())
        assert not page_errors
        assert page.locator("img").count() == 0
        assert page.evaluate("window.injected === undefined")
        assert page.locator("section#execution-summary").is_visible()
        assert page.locator("article.finding").count() == 2
        page.get_by_label("Oracle type").select_option("exception")
        assert page.locator("article.finding:visible").count() == 1
        page.get_by_label("Oracle type").select_option("")
        assert page.locator("article.finding:visible").count() == 2
        page.screenshot(path="/tmp/testing_report.png")

        page.goto((ROOT / "docs/explorer.html").as_uri())
        count = page.locator("article.finding").count()
        assert count == 60
        page.get_by_label("Task or symptom").fill("stale response")
        assert 0 < page.locator("article.finding:visible").count() < count
        page.get_by_label("Task or symptom").fill("")
        page.get_by_label("Family").select_option("repository-workflows")
        assert page.locator("article.finding:visible").count() == 6
        target_card = page.locator("article#review-concurrency-hazards")
        assert not target_card.is_visible()
        page.get_by_label("Family").select_option("")
        assert page.locator("article.finding:visible").count() == count
        assert target_card.is_visible()
        page.get_by_text(
            "review-pr — Review changed behavior and affected contracts.",
            exact=True,
        ).click()
        skill_link = page.locator('a[href="#review-concurrency-hazards"]').first
        assert skill_link.is_visible()
        skill_link.click()
        assert target_card.is_visible()
        assert page.url.endswith("#review-concurrency-hazards")
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot))
        assert not network
        browser.close()
print(
    "PASS: report/explorer filtering, safe text, no external requests, and workflow discovery"
)
