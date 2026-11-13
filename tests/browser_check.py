"""Exercise generated local pages in a real browser, including hostile report text."""
import argparse
import copy
from pathlib import Path
import shutil
import sys
import tempfile

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
    audit["findings"][0]["title"] = '<img src="https://example.invalid/x" onerror="window.injected=true">'
    render(audit, report)
    with sync_playwright() as playwright:
        # An installed system Chromium is useful in managed environments without
        # Playwright downloads; ordinary CI uses its version-matched browser.
        system_browser = shutil.which("chromium") or shutil.which("chromium-browser")
        browser = playwright.chromium.launch(headless=True, executable_path=system_browser)
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        network = []
        page.on("request", lambda request: network.append(request.url) if request.url.startswith(("http:", "https:")) else None)
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
        page.get_by_text("Developer: failure, source, and correction", exact=True).click()
        assert page.get_by_text("No publication fence", exact=False).count() > 0
        assert not network
        page.goto((ROOT / "docs/explorer.html").as_uri())
        count = page.locator("article.finding").count()
        assert count >= 41
        page.get_by_label("Task or symptom").fill("stale response")
        assert 0 < page.locator("article.finding:visible").count() < count
        page.get_by_label("Task or symptom").fill("")
        page.get_by_label("Family").select_option("repository-workflows")
        assert page.locator("article.finding:visible").count() == 6
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot))
        assert not network
        browser.close()
print("PASS: report/explorer filtering, safe text, no external requests, and workflow discovery")
