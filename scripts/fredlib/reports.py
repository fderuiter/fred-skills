from __future__ import annotations

import base64
import hashlib
from html import escape
from pathlib import Path

from .runs import audit_errors, compare

STYLE = """:root{--table-border:#ddd}body{font:16px/1.6 system-ui,sans-serif;margin:auto;max-width:1150px;padding:2rem;color:#172635;background:#f5f7fa}header,section,article{background:white;border:1px solid #dce4eb;border-radius:12px;padding:1.2rem;margin:1rem 0}h1,h2,h3{line-height:1.2}nav{display:flex;gap:1rem;flex-wrap:wrap}a{color:#1556a5}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--table-border);padding:.6rem}.table-container{overflow-x:auto;-webkit-overflow-scrolling:touch}.badge{display:inline-block;padding:.2rem .6rem;border-radius:999px;font-size:.85rem;font-weight:650}.badge-critical,.critical{background:#fed7d7;color:#9b1c1c}.badge-high,.high{background:#feebc8;color:#c05621}.badge-medium,.medium{background:#fefcbf;color:#b7791f}.badge-low,.low{background:#c6f6d5;color:#22543d}.badge-info,.info{background:#bee3f8;color:#2b6cb0}.empty-state{text-align:center;padding:1.5rem;color:#53667a}.muted{color:#53667a}input,select,button{font:inherit;padding:.5rem;border:1px solid #9baec1;border-radius:5px}button{background:#fff;cursor:pointer}label{display:inline-block;margin:.4rem}article[hidden]{display:none}.trace{border-left:3px solid #5380ab;padding-left:1rem}:focus-visible{outline:3px solid #e8a52c}@media(prefers-color-scheme:dark){:root{--table-border:#405164}body{background:#101821;color:#e3ecf5}header,section,article{background:#182531;border-color:#405164}a{color:#95c7ff}.muted{color:#b2c6d9}.empty-state{color:#b2c6d9}input,select,button{background:#101821;color:#e3ecf5;border-color:#405164}.badge-critical,.critical{background:#742a2a;color:#feb2b2}.badge-high,.high{background:#7b341e;color:#fbd38d}.badge-medium,.medium{background:#744210;color:#faf089}.badge-low,.low{background:#22543d;color:#9ae6b4}.badge-info,.info{background:#2a4365;color:#90cdf4}}"""
SCRIPT = """const q=document.querySelector('#query'),s=document.querySelector('#status'),e=document.querySelector('#empty-state'),r=document.querySelector('#reset-filters');function filter(){let n=0;const a=document.querySelectorAll('article.finding');for(const el of a){el.hidden=!(el.textContent.toLowerCase().includes(q.value.toLowerCase())&&(!s.value||el.dataset.status===s.value));if(!el.hidden)n++;}const c=document.querySelector('#count');if(c)c.textContent=n+' '+(c.dataset.noun||'findings')+' shown';if(e)e.hidden=!(a.length>0&&n===0);}if(q)q.addEventListener('input',filter);if(s)s.addEventListener('change',filter);if(r)r.addEventListener('click',()=>{if(q)q.value='';if(s)s.value='';filter();});filter();"""


def text(value) -> str:
    return escape(str(value), quote=True)


def render(audit: dict, output: Path, prior: dict | None = None) -> None:
    errors = audit_errors(audit)
    if errors:
        raise ValueError("Invalid audit ledger: " + "; ".join(errors[:5]))
    evidence = {e["id"]: e for e in audit["evidence"]}
    findings = sorted(
        audit["findings"],
        key=lambda f: (
            ["critical", "high", "medium", "low", "info"].index(f["severity"]),
            f["id"],
        ),
    )
    cards = []
    for f in findings:
        locations = "".join(
            f"<li><code>{text(evidence[eid]['path'])}:{evidence[eid]['line_start']}–{evidence[eid]['line_end']}</code> — {text(evidence[eid]['symbol'])} — {text(evidence[eid]['context'])}</li>"
            for eid in f["evidence_ids"]
        )
        sequence = "".join(f"<li>{text(step)}</li>" for step in f["sequence"])
        criteria = "".join(f"<li>{text(step)}</li>" for step in f["acceptance"])
        cards.append(f'''<article class="finding" data-status="{text(f["status"])}" id="{text(f["id"])}"><h3>{text(f["id"])}: {text(f["title"])}</h3>
<p class="badge badge-{text(f["severity"])} {text(f["severity"])}">{text(f["severity"])} · {text(f["status"])} · confidence {text(f["confidence"])}</p>
<p>{text(f["impact"])}</p><details><summary>Developer: failure, source, and correction</summary><p>{text(f["preconditions"])}</p><ol class="trace">{sequence}</ol><p><b>Expected:</b> {text(f["expected"])}</p><p><b>Actual:</b> {text(f["actual"])}</p><ul>{locations}</ul><p><b>Cause:</b> {text(f["root_cause"])}</p><p><b>Proposed remedy:</b> {text(f["remedy"])}</p></details>
<details><summary>Reviewer: contracts, safeguards, and verification</summary><p>Contracts: {text(", ".join(f["contract_ids"]))}</p><p>{text(f["counterevidence"])}</p><p>Validation: {text(f["validation"]["state"])}</p><pre>{text(chr(10).join(f["validation"]["commands"]))}</pre><p>{text(f["validation"]["result"])}</p></details>
<details><summary>Implementation: acceptance and relationships</summary><ul>{criteria}</ul><p>Related findings: {text(", ".join(f["related_findings"]))}</p><p class="muted">Evidence certainty is separate from remediation state.</p></details></article>''')
    coverage = (
        "".join(
            f"<tr><td>{text(c['topic'])}</td><td>{text(c['scope'])}</td><td>{text(c['status'])}</td><td>{text(c['notes'])}</td><td>{text(c['next_action'])}</td></tr>"
            for c in audit["coverage"]
        )
        or '<tr><td colspan="5" class="empty-state">No coverage recorded.</td></tr>'
    )
    limitations = "".join(f"<li>{text(item)}</li>" for item in audit["limitations"])
    checks = (
        "".join(
            f"<tr><td>{text(c['name'])}</td><td>{text(c['state'])}</td><td><code>{text(c['command'])}</code></td><td>{text(c['result'])}</td></tr>"
            for c in audit["checks"]
        )
        or '<tr><td colspan="4" class="empty-state">No checks recorded.</td></tr>'
    )
    import json

    comparison = (
        text(json.dumps(compare(prior, audit), indent=2))
        if prior
        else "No prior ledger supplied. Missing findings on another run do not prove resolution."
    )
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    csp = f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}'; base-uri 'none'; form-action 'none'; connect-src 'none'"
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}"><title>Repository audit — {text(audit["run_id"])}</title><style>{STYLE}</style></head><body>
<header><p class="muted">fred-skills · local evidence report</p><h1>Repository audit</h1><p>{text(audit["run_id"])} · {text(audit["repository"]["identity"])}</p><p>Revision: <code>{text(audit["repository"]["revision"])}</code> · dirty: {text(audit["repository"]["dirty"])}</p><nav aria-label="Report views"><a href="#executive">Executive</a><a href="#findings">Findings</a><a href="#coverage">Coverage</a><a href="#checks">Checks</a><a href="#comparison">Comparison</a></nav></header>
<section id="executive"><h2>Executive view</h2><p>{len(findings)} finding records; {sum(f["status"] == "confirmed" for f in findings)} reproduced; {sum(f["status"] == "refuted" for f in findings)} refuted. These counts are not a repository safety score.</p><ul>{limitations}</ul></section>
<section id="findings"><h2>Review findings</h2><label for="query">Search findings <input id="query" type="search"></label><label for="status">Evidence status <select id="status"><option value="">All statuses</option>{"".join(f"<option>{s}</option>" for s in ["confirmed", "supported", "needs-validation", "hardening", "refuted"])}</select></label><p id="count" role="status" aria-live="polite"></p><div id="empty-state" class="empty-state" hidden><p>No findings match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>{"".join(cards) or "<p>No findings recorded. Review coverage before drawing conclusions.</p>"}</section>
<section id="coverage"><h2>Coverage and continuation</h2><div class="table-container"><table><thead><tr><th>Topic</th><th>Scope</th><th>Status</th><th>Notes</th><th>Next action</th></tr></thead><tbody>{coverage}</tbody></table></div></section>
<section id="checks"><h2>Executed and unexecuted checks</h2><div class="table-container"><table><thead><tr><th>Check</th><th>State</th><th>Command</th><th>Result</th></tr></thead><tbody>{checks}</tbody></table></div></section><section id="comparison"><h2>Comparison</h2><pre>{comparison}</pre></section><script>{SCRIPT}</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def issue_drafts(audit: dict, output: Path) -> list[str]:
    errors = audit_errors(audit)
    if errors:
        raise ValueError("Invalid ledger; cannot draft issues")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(
            "Issue output directory must be empty; stale drafts will not be mixed with current results"
        )
    findings = {f["id"]: f for f in audit["findings"]}
    evidence = {e["id"]: e for e in audit["evidence"]}
    paths = []
    for issue in audit["issues"]:
        if issue["publication"] != "draft":
            continue
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue["id"]):
            raise ValueError("Issue IDs must be filename-safe")
        sections = [
            f"# {issue['title']}",
            "Publication state: local draft; no external write performed.",
        ]
        for fid in issue["finding_ids"]:
            f = findings[fid]
            sections.extend(
                [
                    f"## {fid}: {f['title']}",
                    f"Evidence: {f['status']}; severity: {f['severity']}; confidence: {f['confidence']}",
                    f["impact"],
                    "### Preconditions",
                    f["preconditions"],
                    "### Expected and actual behavior",
                    "Expected: " + f["expected"],
                    "Actual: " + f["actual"],
                    "### Source evidence",
                    "\n".join(
                        f"- {evidence[eid]['path']}:{evidence[eid]['line_start']}–{evidence[eid]['line_end']} ({evidence[eid]['symbol']}): {evidence[eid]['context']}"
                        for eid in f["evidence_ids"]
                    ),
                    "### Contract and cause",
                    "Contract IDs: " + ", ".join(f["contract_ids"]),
                    f["root_cause"],
                    "### Failure sequence",
                    "\n".join(
                        f"{i}. {step}" for i, step in enumerate(f["sequence"], 1)
                    ),
                    "### Counterevidence",
                    f["counterevidence"],
                    "### Proposed correction",
                    f["remedy"],
                    "### Acceptance",
                    "\n".join("- " + a for a in f["acceptance"]),
                    "### Validation",
                    f["validation"]["state"] + ": " + f["validation"]["result"],
                    "\n".join(
                        "    " + command for command in f["validation"]["commands"]
                    ),
                ]
            )
        sections.append("Dependencies: " + ", ".join(issue["depends_on"]))
        path = output / (issue["id"] + ".md")
        path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
        paths.append(str(path))
    return paths
