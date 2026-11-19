from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from pathlib import Path

import jsonschema

from .common import DOMAIN_DELIVERABLE_SCHEMAS, ROOT, _detect_domain_kind, read_json
from .runs import audit_errors, compare

STYLE = """:root{--table-border:#ddd}body{font:16px/1.6 system-ui,sans-serif;margin:auto;max-width:1150px;padding:2rem;color:#172635;background:#f5f7fa}header,section,article{background:white;border:1px solid #dce4eb;border-radius:12px;padding:1.2rem;margin:1rem 0}h1,h2,h3{line-height:1.2}nav{display:flex;gap:1rem;flex-wrap:wrap}a{color:#1556a5}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--table-border);padding:.6rem}.table-container{overflow-x:auto;-webkit-overflow-scrolling:touch}.badge{display:inline-block;padding:.2rem .6rem;border-radius:999px;font-size:.85rem;font-weight:650}.badge-critical,.critical{background:#fed7d7;color:#9b1c1c}.badge-high,.high{background:#feebc8;color:#7b341e}.badge-medium,.medium{background:#fefcbf;color:#744210}.badge-low,.low{background:#c6f6d5;color:#22543d}.badge-info,.info,.badge-advisory,.advisory{background:#bee3f8;color:#2a4365}.empty-state{text-align:center;padding:1.5rem;color:#53667a}.muted{color:#53667a}input,select,button{font:inherit;padding:.5rem;border:1px solid #9baec1;border-radius:5px}button{background:#fff;cursor:pointer}label{display:inline-block;margin:.4rem}[hidden]{display:none}.trace{border-left:3px solid #5380ab;padding-left:1rem}:focus-visible{outline:3px solid #1556a5}@media(prefers-color-scheme:dark){:root{--table-border:#405164}body{background:#101821;color:#e3ecf5}header,section,article{background:#182531;border-color:#405164}a{color:#95c7ff}.muted{color:#b2c6d9}.empty-state{color:#b2c6d9}input,select,button{background:#101821;color:#e3ecf5;border-color:#405164}:focus-visible{outline:3px solid #95c7ff}.badge-critical,.critical{background:#742a2a;color:#feb2b2}.badge-high,.high{background:#7b341e;color:#fbd38d}.badge-medium,.medium{background:#744210;color:#faf089}.badge-low,.low{background:#22543d;color:#9ae6b4}.badge-info,.info,.badge-advisory,.advisory{background:#2a4365;color:#90cdf4}}"""
SCRIPT = """const q=document.querySelector('#query'),s=document.querySelector('#status'),e=document.querySelector('#empty-state'),r=document.querySelector('#reset-filters');function filter(){const qv=q?q.value.toLowerCase():'',toks=qv.split(/[\\s-]+/).filter(Boolean),sv=s?s.value:'',tasks=document.querySelectorAll('details.task'),ms=new Set();let tn=0,n=0;for(const el of tasks){const suites=(el.dataset.suites||'').split(' ').filter(Boolean),fOk=!sv||suites.includes(sv),hay=(el.dataset.search||el.textContent||'').toLowerCase(),qOk=toks.every(t=>hay.includes(t));if(toks.length&&qOk){for(const sk of(el.dataset.skills||'').split(' '))if(sk)ms.add(sk);}el.hidden=!(fOk&&qOk);if(!el.hidden)tn++;}const a=document.querySelectorAll('article.finding');for(const el of a){const fOk=!sv||el.dataset.status===sv,txt=el.textContent.toLowerCase(),qOk=!toks.length||ms.has(el.id)||toks.every(t=>txt.includes(t));el.hidden=!(fOk&&qOk);if(!el.hidden)n++;}for(const sec of document.querySelectorAll('section[data-filter-section]')){sec.hidden=!sec.querySelector('details.task:not([hidden]),article.finding:not([hidden])');}const c=document.querySelector('#count');if(c)c.textContent=tasks.length?tn+' tasks, '+n+' '+(c.dataset.noun||'skills')+' shown':n+' '+(c.dataset.noun||'findings')+' shown';if(e)e.hidden=!((a.length+tasks.length)>0&&n===0&&tn===0);}function reveal(raw){if(!raw)return;let id=raw;try{id=decodeURIComponent(raw);}catch{}const el=document.getElementById(id);if(!el||!el.classList.contains('finding'))return;if(el.hidden){if(s&&s.value&&el.dataset.status!==s.value)s.value='';filter();if(el.hidden&&q&&q.value){q.value='';filter();}}el.scrollIntoView();}function revealHash(){if(location.hash.length>1)reveal(location.hash.slice(1));}document.addEventListener('click',ev=>{const link=ev.target instanceof Element?ev.target.closest('a[href^="#"]'):null;if(link){const h=link.getAttribute('href')||'';if(h.length>1)reveal(h.slice(1));}});window.addEventListener('hashchange',revealHash);if(q)q.addEventListener('input',filter);if(s)s.addEventListener('change',filter);if(r)r.addEventListener('click',()=>{if(q)q.value='';if(s)s.value='';filter();});filter();revealHash();"""


def text(value) -> str:
    return escape(str(value), quote=True)


def _csp_value() -> str:
    script_hash = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{script_hash}'; base-uri 'none'; "
        f"form-action 'none'; connect-src 'none'"
    )


def _page_shell(title: str, body_html: str) -> str:
    csp = _csp_value()
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">'
        f"<title>{title}</title><style>{STYLE}</style></head><body>\n"
        f"{body_html}<script>{SCRIPT}</script></body></html>"
    )


def _validate_domain_payload(payload: dict, kind: str) -> None:
    schema_rel = DOMAIN_DELIVERABLE_SCHEMAS[f"{kind}.json"]
    schema = read_json(ROOT / schema_rel)
    errors = [
        str(err.message)
        for err in jsonschema.Draft202012Validator(schema).iter_errors(payload)
    ]
    if errors:
        raise ValueError(f"Invalid {kind} artifact: " + "; ".join(errors[:5]))


def _render_architecture_html(doc: dict) -> str:
    status_badges = {
        "accepted": "low",
        "draft": "info",
        "superseded": "medium",
        "deprecated": "high",
    }
    status_tone = status_badges.get(doc["status"], "info")
    boundaries = doc.get("boundaries", [])
    decisions = doc.get("decisions", [])
    boundary_rows = (
        "".join(
            f"<tr><td>{text(b['name'])}</td>"
            f'<td><span class="badge badge-info info">{text(b["type"])}</span></td>'
            f"<td>{text(b.get('description', ''))}</td>"
            f"<td>{'<ul>' + ''.join(f'<li>{text(inv)}</li>' for inv in b.get('invariants', [])) + '</ul>' if b.get('invariants') else '<span class="muted">None recorded</span>'}</td></tr>"
            for b in boundaries
        )
        or '<tr><td colspan="4" class="empty-state">No system boundaries recorded.</td></tr>'
    )
    if decisions:
        cards = []
        for d in decisions:
            consequences = (
                "<ul>"
                + "".join(
                    f"<li>{text(item)}</li>" for item in d.get("consequences", [])
                )
                + "</ul>"
                if d.get("consequences")
                else '<p class="muted">No explicit consequences recorded.</p>'
            )
            context_html = (
                f"<p><b>Context:</b> {text(d['context'])}</p>"
                if d.get("context")
                else ""
            )
            cards.append(
                f'<article class="finding" data-status="{text(doc["status"])}" id="{text(d["id"])}">'
                f"<h3>{text(d['id'])}: {text(d['title'])}</h3>"
                f"{context_html}"
                f"<p><b>Decision:</b> {text(d['decision'])}</p>"
                f"<details><summary>Consequences and trade-offs</summary>{consequences}</details>"
                f"</article>"
            )
        decisions_body = (
            '<label for="query">Search decisions <input id="query" type="search"></label>'
            '<p id="count" data-noun="decisions" role="status" aria-live="polite"></p>'
            '<div id="empty-state" class="empty-state" hidden><p>No decisions match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            + "".join(cards)
        )
    else:
        decisions_body = "<p>No architectural decisions recorded.</p>"
    body = (
        f'<header><p class="muted">fred-skills · software architecture report</p>'
        f"<h1>{text(doc['title'])}</h1>"
        f'<p><span class="badge badge-{status_tone} {status_tone}">{text(doc["status"])}</span> · schema {text(doc["schema_version"])}</p>'
        f'<nav aria-label="Report views"><a href="#executive">Overview</a><a href="#boundaries">Boundaries</a><a href="#decisions">Decisions</a></nav></header>\n'
        f'<section id="executive"><h2>Architecture overview</h2><p>{len(boundaries)} system boundaries; {len(decisions)} decision records; status: <code>{text(doc["status"])}</code>.</p></section>\n'
        f'<section id="boundaries"><h2>System boundaries</h2><div class="table-container"><table><thead><tr><th>Boundary</th><th>Type</th><th>Description</th><th>Invariants</th></tr></thead><tbody>{boundary_rows}</tbody></table></div></section>\n'
        f'<section id="decisions"><h2>Architectural decisions</h2>{decisions_body}</section>'
    )
    return _page_shell(f"Software architecture — {text(doc['title'])}", body)


def _render_review_html(doc: dict) -> str:
    verdict_badges = {
        "approve": "low",
        "request_changes": "high",
        "comment": "info",
    }
    verdict_tone = verdict_badges.get(doc["verdict"], "info")
    severity_order = ["critical", "high", "medium", "low", "advisory"]
    findings = sorted(
        doc["findings"],
        key=lambda f: (severity_order.index(f["severity"]), f["id"]),
    )
    if findings:
        cards = []
        for f in findings:
            loc = (
                f"{text(f['file'])}:{text(f['line'])}"
                if "line" in f
                else text(f["file"])
            )
            rec = (
                f"<p><b>Recommendation:</b> {text(f['recommendation'])}</p>"
                if f.get("recommendation")
                else ""
            )
            cards.append(
                f'<article class="finding" data-status="{text(f["severity"])}" id="{text(f["id"])}">'
                f"<h3>{text(f['id'])}: {text(f['title'])}</h3>"
                f'<p class="badge badge-{text(f["severity"])} {text(f["severity"])}">{text(f["severity"])}</p>'
                f"<p><b>Location:</b> <code>{loc}</code></p>"
                f"<p>{text(f['description'])}</p>"
                f"{rec}</article>"
            )
        status_options = "".join(f"<option>{s}</option>" for s in severity_order)
        findings_body = (
            '<label for="query">Search findings <input id="query" type="search"></label>'
            f'<label for="status">Severity <select id="status"><option value="">All severities</option>{status_options}</select></label>'
            '<p id="count" data-noun="findings" role="status" aria-live="polite"></p>'
            '<div id="empty-state" class="empty-state" hidden><p>No findings match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            + "".join(cards)
        )
    else:
        findings_body = "<p>No review findings recorded.</p>"
    body = (
        f'<header><p class="muted">fred-skills · code review report</p>'
        f"<h1>Code review</h1>"
        f'<p><span class="badge badge-{verdict_tone} {verdict_tone}">{text(doc["verdict"])}</span> · {len(findings)} findings · schema {text(doc["schema_version"])}</p>'
        f'<nav aria-label="Report views"><a href="#executive">Summary</a><a href="#findings">Findings</a></nav></header>\n'
        f'<section id="executive"><h2>Review summary</h2><p>{text(doc["summary"])}</p></section>\n'
        f'<section id="findings"><h2>Review findings</h2>{findings_body}</section>'
    )
    return _page_shell(f"Code review — {text(doc['verdict'])}", body)


def _render_testing_html(doc: dict) -> str:
    test_cases = doc["test_cases"]
    oracle_types = ["state", "exception", "output", "boundary"]
    verified_count = sum(tc.get("verified") is True for tc in test_cases)
    if test_cases:
        cards = []
        for tc in test_cases:
            if tc.get("verified") is True:
                v_badge = '<span class="badge badge-low low">verified</span>'
            elif tc.get("verified") is False:
                v_badge = '<span class="badge badge-medium medium">unverified</span>'
            else:
                v_badge = '<span class="badge badge-info info">pending</span>'
            cards.append(
                f'<article class="finding" data-status="{text(tc["oracle_type"])}" id="{text(tc["id"])}">'
                f"<h3>{text(tc['id'])}: {text(tc['name'])}</h3>"
                f'<p><span class="badge badge-info info">oracle: {text(tc["oracle_type"])}</span> {v_badge}</p>'
                f"<p>{text(tc['description'])}</p></article>"
            )
        oracle_options = "".join(f"<option>{o}</option>" for o in oracle_types)
        cases_body = (
            '<label for="query">Search test cases <input id="query" type="search"></label>'
            f'<label for="status">Oracle type <select id="status"><option value="">All oracles</option>{oracle_options}</select></label>'
            '<p id="count" data-noun="test cases" role="status" aria-live="polite"></p>'
            '<div id="empty-state" class="empty-state" hidden><p>No test cases match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            + "".join(cards)
        )
    else:
        cases_body = "<p>No test cases recorded.</p>"
    body = (
        f'<header><p class="muted">fred-skills · automated testing report</p>'
        f"<h1>Automated testing — {text(doc['component'])}</h1>"
        f"<p>Framework: <code>{text(doc['framework'])}</code> · {len(test_cases)} test cases · schema {text(doc['schema_version'])}</p>"
        f'<nav aria-label="Report views"><a href="#executive">Scope</a><a href="#test-cases">Test cases</a></nav></header>\n'
        f'<section id="executive"><h2>Test suite scope</h2><p>Component: <code>{text(doc["component"])}</code> · Framework: <code>{text(doc["framework"])}</code> · {len(test_cases)} test cases ({verified_count} verified).</p></section>\n'
        f'<section id="test-cases"><h2>Test cases</h2>{cases_body}</section>'
    )
    return _page_shell(f"Automated testing — {text(doc['component'])}", body)


def _render_audit_html(audit: dict, prior: dict | None = None) -> str:
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
    comparison = (
        text(json.dumps(compare(prior, audit), indent=2))
        if prior
        else "No prior ledger supplied. Missing findings on another run do not prove resolution."
    )
    if len(findings) > 0:
        status_options = "".join(
            f"<option>{s}</option>"
            for s in [
                "confirmed",
                "supported",
                "needs-validation",
                "hardening",
                "refuted",
            ]
        )
        findings_body = (
            f'<label for="query">Search findings <input id="query" type="search"></label>'
            f'<label for="status">Evidence status <select id="status"><option value="">All statuses</option>{status_options}</select></label>'
            f'<p id="count" role="status" aria-live="polite"></p>'
            f'<div id="empty-state" class="empty-state" hidden><p>No findings match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            f"{''.join(cards)}"
        )
    else:
        findings_body = (
            "<p>No findings recorded. Review coverage before drawing conclusions.</p>"
        )
    body = f"""<header><p class="muted">fred-skills · local evidence report</p><h1>Repository audit</h1><p>{text(audit["run_id"])} · {text(audit["repository"]["identity"])}</p><p>Revision: <code>{text(audit["repository"]["revision"])}</code> · dirty: {text(audit["repository"]["dirty"])}</p><nav aria-label="Report views"><a href="#executive">Executive</a><a href="#findings">Findings</a><a href="#coverage">Coverage</a><a href="#checks">Checks</a><a href="#comparison">Comparison</a></nav></header>
<section id="executive"><h2>Executive view</h2><p>{len(findings)} finding records; {sum(f["status"] == "confirmed" for f in findings)} reproduced; {sum(f["status"] == "refuted" for f in findings)} refuted. These counts are not a repository safety score.</p><ul>{limitations}</ul></section>
<section id="findings"><h2>Review findings</h2>{findings_body}</section>
<section id="coverage"><h2>Coverage and continuation</h2><div class="table-container"><table><thead><tr><th>Topic</th><th>Scope</th><th>Status</th><th>Notes</th><th>Next action</th></tr></thead><tbody>{coverage}</tbody></table></div></section>
<section id="checks"><h2>Executed and unexecuted checks</h2><div class="table-container"><table><thead><tr><th>Check</th><th>State</th><th>Command</th><th>Result</th></tr></thead><tbody>{checks}</tbody></table></div></section><section id="comparison"><h2>Comparison</h2><pre>{comparison}</pre></section>"""
    return _page_shell(f"Repository audit — {text(audit['run_id'])}", body)


def render(audit: dict, output: Path, prior: dict | None = None) -> Path:
    domain_kind = _detect_domain_kind(audit)
    if domain_kind is not None:
        if prior is not None:
            raise ValueError(
                f"Prior comparison ('--prior') requires 'audit.json' ledgers; domain artifact '{domain_kind}.json' is not supported"
            )
        _validate_domain_payload(audit, domain_kind)
        renderers = {
            "architecture": _render_architecture_html,
            "review": _render_review_html,
            "testing": _render_testing_html,
        }
        html = renderers[domain_kind](audit)
    else:
        html = _render_audit_html(audit, prior=prior)
    output = Path(output)
    if output.is_dir() or not output.suffix:
        output.mkdir(parents=True, exist_ok=True)
        target = output / "report.html"
    else:
        target = output
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    return target


def issue_drafts(audit: dict, output: Path, format: str = "markdown") -> list[str]:
    if format not in ("markdown", "json"):
        raise ValueError(f"Unsupported issue format: {format}")
    domain_kind = _detect_domain_kind(audit)
    if domain_kind is not None:
        raise ValueError(
            f"Issue drafts ('issues') require an 'audit.json' ledger; domain artifact '{domain_kind}.json' is not supported"
        )
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
        body_text = "\n\n".join(sections)
        if format == "json":
            payload = {
                "title": issue["title"],
                "body": body_text,
                "labels": list(issue.get("labels", [])),
                "assignees": list(issue.get("assignees", [])),
            }
            path = output / (issue["id"] + ".json")
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        else:
            path = output / (issue["id"] + ".md")
            path.write_text(body_text + "\n", encoding="utf-8")
        paths.append(str(path))
    return paths
