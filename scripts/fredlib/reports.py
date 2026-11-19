from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from pathlib import Path
from typing import Any

import jsonschema

from .common import (
    DOMAIN_DELIVERABLE_SCHEMAS,
    ROOT,
    FredCliError,
    _detect_domain_kind,
    read_json,
)
from .runs import audit_errors, compare, fingerprint

STYLE = """:root{--table-border:#ddd}body{font:16px/1.6 system-ui,sans-serif;margin:auto;max-width:1150px;padding:2rem;color:#172635;background:#f5f7fa}header,section,article{background:white;border:1px solid #dce4eb;border-radius:12px;padding:1.2rem;margin:1rem 0}h1,h2,h3{line-height:1.2}h1,h2,h3,p,li,summary{overflow-wrap:anywhere}nav{display:flex;gap:1rem;flex-wrap:wrap}a{color:#1556a5}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--table-border);padding:.6rem}.table-container{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}.badges{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}.badge{display:inline-block;padding:.2rem .6rem;border-radius:999px;font-size:.85rem;font-weight:650;box-sizing:border-box;max-width:100%}.badge-critical,.critical{background:#fed7d7;color:#9b1c1c}.badge-high,.high{background:#feebc8;color:#7b341e}.badge-medium,.medium{background:#fefcbf;color:#744210}.badge-low,.low{background:#c6f6d5;color:#22543d}.badge-info,.info,.badge-advisory,.advisory{background:#bee3f8;color:#2a4365}.callout{border-left:4px solid #5380ab;background:#edf2f7;padding:.75rem 1rem;border-radius:6px;margin:.8rem 0}.callout p{margin:.25rem 0}.empty-state{text-align:center;padding:1.5rem;color:#53667a}.muted{color:#53667a}input,select,button{font:inherit;padding:.5rem;border:1px solid #9baec1;border-radius:5px;box-sizing:border-box;max-width:100%}button{background:#fff;cursor:pointer}label{display:inline-block;margin:.4rem;box-sizing:border-box;max-width:100%}#toggle-details{margin:.4rem}article[hidden]{display:none}.trace{border-left:3px solid #5380ab;padding-left:1rem}:focus-visible{outline:3px solid #1556a5}@media (max-width: 640px){body{padding:.75rem}header,section,article{padding:.85rem;margin:.75rem 0}label{display:block;margin:.4rem 0}input,select{width:100%}#toggle-details{margin:.4rem 0}}@media(prefers-color-scheme:dark){:root{--table-border:#405164}body{background:#101821;color:#e3ecf5}header,section,article{background:#182531;border-color:#405164}a{color:#95c7ff}.muted{color:#b2c6d9}.empty-state{color:#b2c6d9}.callout{background:#1e2f3f;border-left-color:#95c7ff}input,select,button{background:#101821;color:#e3ecf5;border-color:#405164}:focus-visible{outline:3px solid #95c7ff}.badge-critical,.critical{background:#742a2a;color:#feb2b2}.badge-high,.high{background:#7b341e;color:#fbd38d}.badge-medium,.medium{background:#744210;color:#faf089}.badge-low,.low{background:#22543d;color:#9ae6b4}.badge-info,.info,.badge-advisory,.advisory{background:#2a4365;color:#90cdf4}}@media print{body{background:#fff;color:#172635;padding:0;max-width:none}header,section,article{background:#fff;color:#172635;border-color:#dce4eb}.callout{background:#edf2f7;color:#172635;border-left-color:#5380ab}label[for="query"],label[for="severity"],label[for="status"],label[for="remediation"],#query,#severity,#status,#remediation,#toggle-details,#empty-state,#reset-filters{display:none!important}}"""
SCRIPT = """const q=document.querySelector('#query'),sev=document.querySelector('#severity'),s=document.querySelector('#status'),rem=document.querySelector('#remediation'),e=document.querySelector('#empty-state'),r=document.querySelector('#reset-filters'),t=document.querySelector('#toggle-details');function visibleDetails(){return[...document.querySelectorAll('article.finding:not([hidden]) details')];}function syncToggle(){if(!t)return;const v=visibleDetails(),x=v.length>0&&v.every(d=>d.open);t.setAttribute('aria-expanded',x?'true':'false');t.textContent=x?'Collapse all':'Expand all';}function filter(){const qv=q?q.value.toLowerCase():'',sevv=sev?sev.value:'',sv=s?s.value:'',remv=rem?rem.value:'';let n=0;const a=document.querySelectorAll('article.finding');for(const el of a){el.hidden=!(el.textContent.toLowerCase().includes(qv)&&(!sevv||el.dataset.severity===sevv)&&(!sv||el.dataset.status===sv)&&(!remv||el.dataset.remediation===remv));if(!el.hidden)n++;for(const d of el.querySelectorAll('details')){if(qv){if(!('preSearchOpen' in d.dataset))d.dataset.preSearchOpen=d.open?'1':'0';if(!('prePrintOpen' in d.dataset))d.open=d.dataset.preSearchOpen==='1'||(!el.hidden&&d.textContent.toLowerCase().includes(qv));}else if('preSearchOpen' in d.dataset){const p=d.dataset.preSearchOpen==='1';delete d.dataset.preSearchOpen;if(!('prePrintOpen' in d.dataset))d.open=p;else d.dataset.prePrintOpen=p?'1':'0';}}}const c=document.querySelector('#count');if(c){const base=c.dataset.noun||'findings';c.textContent=n+' '+(n===1?base.replace(/s$/,''):base)+' shown';}if(e)e.hidden=!(a.length>0&&n===0);syncToggle();}function openPrint(){for(const d of document.querySelectorAll('details')){if(!('prePrintOpen' in d.dataset))d.dataset.prePrintOpen=d.open?'1':'0';d.open=true;}syncToggle();}function restorePrint(){for(const d of document.querySelectorAll('details')){if('prePrintOpen' in d.dataset){d.open=d.dataset.prePrintOpen==='1';delete d.dataset.prePrintOpen;}}syncToggle();}if(q)q.addEventListener('input',filter);if(sev)sev.addEventListener('change',filter);if(s)s.addEventListener('change',filter);if(rem)rem.addEventListener('change',filter);if(r)r.addEventListener('click',()=>{if(q)q.value='';if(sev)sev.value='';if(s)s.value='';if(rem)rem.value='';filter();if(q)q.focus();});if(t)t.addEventListener('click',()=>{const v=visibleDetails(),x=!(v.length>0&&v.every(d=>d.open));for(const d of v){d.open=x;if('preSearchOpen' in d.dataset)d.dataset.preSearchOpen=x?'1':'0';}syncToggle();});document.addEventListener('click',ev=>{if(ev.target&&ev.target.closest&&ev.target.closest('summary'))queueMicrotask(syncToggle);});document.addEventListener('toggle',syncToggle,true);window.addEventListener('beforeprint',openPrint);window.addEventListener('afterprint',restorePrint);const mq=window.matchMedia?window.matchMedia('print'):null;if(mq&&mq.addEventListener)mq.addEventListener('change',ev=>ev.matches?openPrint():restorePrint());filter();if(mq&&mq.matches)openPrint();"""

_SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
_SEVERITY_LABELS: dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Info",
}
_STATUS_ORDER: tuple[str, ...] = (
    "confirmed",
    "supported",
    "needs-validation",
    "hardening",
    "refuted",
)
_STATUS_LABELS: dict[str, str] = {
    "confirmed": "Confirmed",
    "supported": "Supported",
    "needs-validation": "Needs validation",
    "hardening": "Hardening",
    "refuted": "Refuted",
}
_REMEDIATION_ORDER: tuple[str, ...] = (
    "open",
    "in-progress",
    "fixed-pending-verification",
    "verified-fixed",
    "regressed",
    "accepted-risk",
)
_REMEDIATION_LABELS: dict[str, str] = {
    "open": "Open",
    "in-progress": "In progress",
    "fixed-pending-verification": "Fixed pending verification",
    "verified-fixed": "Verified fixed",
    "regressed": "Regressed",
    "accepted-risk": "Accepted risk",
}
_REMEDIATION_TONES: dict[str, str] = {
    "open": "medium",
    "in-progress": "info",
    "fixed-pending-verification": "info",
    "verified-fixed": "low",
    "regressed": "critical",
    "accepted-risk": "medium",
}


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
    components = doc.get("components", [])
    interfaces = doc.get("interfaces", [])
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
    component_rows = (
        "".join(
            f"<tr><td>{text(c['name'])}</td>"
            f"<td>{text(c['responsibility'])}</td>"
            f"<td>{', '.join(f'<code>{text(m)}</code>' for m in c.get('owned_models', [])) or '<span class="muted">None recorded</span>'}</td>"
            f"<td>{', '.join(f'<code>{text(dep)}</code>' for dep in c.get('dependencies', [])) or '<span class="muted">None recorded</span>'}</td></tr>"
            for c in components
        )
        or '<tr><td colspan="4" class="empty-state">No components recorded.</td></tr>'
    )
    interface_rows = (
        "".join(
            f"<tr><td>{text(iface['name'])}</td>"
            f'<td><span class="badge badge-info info">{text(iface["protocol"])}</span></td>'
            f"<td>{text(iface.get('producer', '')) or '<span class="muted">Unspecified</span>'}</td>"
            f"<td>{', '.join(text(con) for con in iface.get('consumers', [])) or '<span class="muted">None recorded</span>'}</td>"
            f"<td>{text(iface.get('contract', ''))}</td>"
            f"<td>{text(iface.get('compatibility', ''))}</td></tr>"
            for iface in interfaces
        )
        or '<tr><td colspan="6" class="empty-state">No interfaces recorded.</td></tr>'
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
            '<button id="toggle-details" type="button" aria-expanded="false">Expand all</button>'
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
        f'<nav aria-label="Report views"><a href="#executive">Overview</a><a href="#boundaries">Boundaries</a><a href="#components">Components</a><a href="#interfaces">Interfaces</a><a href="#decisions">Decisions</a></nav></header>\n'
        f'<section id="executive"><h2>Architecture overview</h2><p>{len(boundaries)} system boundaries; {len(components)} components; {len(interfaces)} interfaces; {len(decisions)} decision records; status: <code>{text(doc["status"])}</code>.</p></section>\n'
        f'<section id="boundaries"><h2>System boundaries</h2><div class="table-container"><table><thead><tr><th>Boundary</th><th>Type</th><th>Description</th><th>Invariants</th></tr></thead><tbody>{boundary_rows}</tbody></table></div></section>\n'
        f'<section id="components"><h2>Components</h2><div class="table-container"><table><thead><tr><th>Component</th><th>Responsibility</th><th>Owned models</th><th>Dependencies</th></tr></thead><tbody>{component_rows}</tbody></table></div></section>\n'
        f'<section id="interfaces"><h2>Interfaces</h2><div class="table-container"><table><thead><tr><th>Interface</th><th>Protocol</th><th>Producer</th><th>Consumers</th><th>Contract</th><th>Compatibility</th></tr></thead><tbody>{interface_rows}</tbody></table></div></section>\n'
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
    priority_tones = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }
    if test_cases:
        cards = []
        for tc in test_cases:
            if tc.get("verified") is True:
                v_badge = '<span class="badge badge-low low">verified</span>'
            elif tc.get("verified") is False:
                v_badge = '<span class="badge badge-medium medium">unverified</span>'
            else:
                v_badge = '<span class="badge badge-info info">pending</span>'
            prio = tc.get("priority")
            p_tone = priority_tones.get(prio, "info") if isinstance(prio, str) else ""
            p_badge = (
                f' <span class="badge badge-{p_tone} {p_tone}">priority: {text(prio)}</span>'
                if isinstance(prio, str) and prio
                else ""
            )
            cards.append(
                f'<article class="finding" data-status="{text(tc["oracle_type"])}" id="{text(tc["id"])}">'
                f"<h3>{text(tc['id'])}: {text(tc['name'])}</h3>"
                f'<p><span class="badge badge-info info">oracle: {text(tc["oracle_type"])}</span> {v_badge}{p_badge}</p>'
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

    exec_summary = doc.get("execution_summary")
    state_tones = {
        "passed": "low",
        "failed": "high",
        "partial": "medium",
        "not_run": "info",
    }
    if isinstance(exec_summary, dict):
        exec_state = str(exec_summary.get("state", "not_run"))
        exec_tone = state_tones.get(exec_state, "info")
        counts_parts = [
            f"{label}: {text(exec_summary[key])}"
            for key, label in (
                ("total", "Total"),
                ("passed", "Passed"),
                ("failed", "Failed"),
                ("skipped", "Skipped"),
            )
            if key in exec_summary
        ]
        counts_html = f" · {' · '.join(counts_parts)}" if counts_parts else ""
        cmds = exec_summary.get("commands", [])
        cmds_html = (
            "<p><b>Commands:</b> "
            + ", ".join(f"<code>{text(cmd)}</code>" for cmd in cmds)
            + "</p>"
            if cmds
            else ""
        )
        notes_html = (
            f"<p><b>Notes:</b> {text(exec_summary['notes'])}</p>"
            if exec_summary.get("notes")
            else ""
        )
        exec_section = (
            f'<section id="execution-summary"><h2>Execution summary</h2>'
            f'<p><span class="badge badge-{exec_tone} {exec_tone}">{text(exec_state)}</span>{counts_html}</p>'
            f"{cmds_html}{notes_html}</section>\n"
        )
    else:
        exec_section = (
            '<section id="execution-summary"><h2>Execution summary</h2>'
            '<p class="muted">No execution summary recorded.</p></section>\n'
        )

    body = (
        f'<header><p class="muted">fred-skills · automated testing report</p>'
        f"<h1>Automated testing — {text(doc['component'])}</h1>"
        f"<p>Framework: <code>{text(doc['framework'])}</code> · {len(test_cases)} test cases · schema {text(doc['schema_version'])}</p>"
        f'<nav aria-label="Report views"><a href="#executive">Scope</a><a href="#execution-summary">Execution</a><a href="#test-cases">Test cases</a></nav></header>\n'
        f'<section id="executive"><h2>Test suite scope</h2><p>Component: <code>{text(doc["component"])}</code> · Framework: <code>{text(doc["framework"])}</code> · {len(test_cases)} test cases ({verified_count} verified).</p></section>\n'
        f"{exec_section}"
        f'<section id="test-cases"><h2>Test cases</h2>{cases_body}</section>'
    )
    return _page_shell(f"Automated testing — {text(doc['component'])}", body)


def _index_remediation(
    remediation: dict[str, Any] | None, audit: dict[str, Any]
) -> tuple[dict[str, dict[str, str]], bool]:
    if remediation is None:
        return {}, False
    if (
        not isinstance(remediation, dict)
        or "records" not in remediation
        or not isinstance(remediation["records"], list)
    ):
        raise ValueError("Invalid remediation ledger: expected a 'records' list")
    raw_records = remediation["records"]
    finding_ids = {f["id"] for f in audit["findings"]}
    fp_to_ids: dict[str, list[str]] = {}
    for finding in audit["findings"]:
        fp_to_ids.setdefault(fingerprint(finding, audit), []).append(finding["id"])
    latest: dict[str, dict[str, str]] = {}
    for rec in raw_records:
        state = rec.get("state") if isinstance(rec, dict) else None
        if not isinstance(rec, dict) or state not in _REMEDIATION_LABELS:
            raise ValueError(f"Unknown remediation state: {state!r}")
        note = str(rec.get("note", ""))
        fid = rec.get("finding_id")
        fp = rec.get("fingerprint")
        matched_id: str | None = None
        if isinstance(fid, str) and fid in finding_ids:
            matched_id = fid
        elif isinstance(fp, str) and len(fp_to_ids.get(fp, [])) == 1:
            matched_id = fp_to_ids[fp][0]
        if matched_id is not None:
            latest[matched_id] = {"state": state, "note": note}
    return latest, len(raw_records) > 0


def _severity_badge_html(sev: str) -> str:
    label = _SEVERITY_LABELS.get(sev, sev)
    return f'<span class="badge badge-{text(sev)} {text(sev)}">{text(label)}</span>'


def _collision_summary_html(
    before_ids: list[str],
    after_ids: list[str],
    prior_findings: dict[str, dict[str, Any]],
    curr_findings: dict[str, dict[str, Any]],
    prior_evidence: dict[str, dict[str, Any]],
    curr_evidence: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    paired: list[tuple[dict[str, Any], dict[str, dict[str, Any]]]] = [
        *(
            (curr_findings[fid], curr_evidence)
            for fid in after_ids
            if fid in curr_findings
        ),
        *(
            (prior_findings[fid], prior_evidence)
            for fid in before_ids
            if fid in prior_findings
        ),
    ]
    topics = list(
        dict.fromkeys(f.get("topic", "") for f, _ in paired if f.get("topic"))
    )
    locs = sorted(
        {
            (ev[eid]["path"], ev[eid]["symbol"])
            for f, ev in paired
            for eid in f.get("evidence_ids", [])
            if eid in ev
        }
    )
    topic_html = (
        text(", ".join(topics)) if topics else '<span class="muted">Unspecified</span>'
    )
    loc_html = (
        ", ".join(
            f"<code>{text(p)}</code> (<code>{text(sym)}</code>)" for p, sym in locs
        )
        if locs
        else '<span class="muted">No location recorded</span>'
    )
    return topic_html, loc_html


def _render_comparison_html(prior: dict[str, Any] | None, audit: dict[str, Any]) -> str:
    if prior is None:
        return "<p>No prior ledger supplied. Missing findings on another run do not prove resolution.</p>"
    diff = compare(prior, audit)
    prior_findings = {f["id"]: f for f in prior.get("findings", [])}
    curr_findings = {f["id"]: f for f in audit.get("findings", [])}
    prior_evidence = {e["id"]: e for e in prior.get("evidence", [])}
    curr_evidence = {e["id"]: e for e in audit.get("evidence", [])}

    introduced_rows = (
        "".join(
            f'<tr><td><a href="#{text(fid)}">{text(fid)}</a></td>'
            f"<td>{_severity_badge_html(curr_findings[fid]['severity']) if fid in curr_findings else '—'}</td>"
            f"<td>{text(_STATUS_LABELS.get(curr_findings[fid]['status'], curr_findings[fid]['status'])) if fid in curr_findings else '—'}</td>"
            f"<td>{text(curr_findings[fid]['title']) if fid in curr_findings else '—'}</td></tr>"
            for fid in diff["introduced"]
        )
        or '<tr><td colspan="4" class="empty-state">No introduced findings.</td></tr>'
    )
    changed_rows = (
        "".join(
            f'<tr><td><a href="#{text(item["after"])}">{text(item["after"])}</a></td>'
            f"<td><code>{text(item['before'])}</code></td>"
            f"<td>{_severity_badge_html(curr_findings[item['after']]['severity']) if item['after'] in curr_findings else '—'}</td>"
            f"<td>{text(_STATUS_LABELS.get(item['from'], item['from']))} → {text(_STATUS_LABELS.get(item['to'], item['to']))}</td>"
            f"<td>{text(curr_findings[item['after']]['title']) if item['after'] in curr_findings else '—'}</td></tr>"
            for item in diff["status_changed"]
        )
        or '<tr><td colspan="5" class="empty-state">No status changes.</td></tr>'
    )
    persisting_rows = (
        "".join(
            f'<tr><td><a href="#{text(item["after"])}">{text(item["after"])}</a></td>'
            f"<td><code>{text(item['before'])}</code></td>"
            f"<td>{_severity_badge_html(curr_findings[item['after']]['severity']) if item['after'] in curr_findings else '—'}</td>"
            f"<td>{text(_STATUS_LABELS.get(curr_findings[item['after']]['status'], curr_findings[item['after']]['status'])) if item['after'] in curr_findings else '—'}</td>"
            f"<td>{text(curr_findings[item['after']]['title']) if item['after'] in curr_findings else '—'}</td></tr>"
            for item in diff["persisting"]
        )
        or '<tr><td colspan="5" class="empty-state">No persisting findings.</td></tr>'
    )
    absent_rows = (
        "".join(
            f"<tr><td><code>{text(fid)}</code></td>"
            f"<td>{_severity_badge_html(prior_findings[fid]['severity']) if fid in prior_findings else '—'}</td>"
            f"<td>{text(_STATUS_LABELS.get(prior_findings[fid]['status'], prior_findings[fid]['status'])) if fid in prior_findings else '—'}</td>"
            f"<td>{text(prior_findings[fid]['title']) if fid in prior_findings else '—'}</td></tr>"
            for fid in diff["not_observed"]
        )
        or '<tr><td colspan="4" class="empty-state">No absent prior findings.</td></tr>'
    )
    ambiguous_rows_list: list[str] = []
    for item in diff["ambiguous"]:
        after_links = (
            ", ".join(
                f'<a href="#{text(fid)}">{text(fid)}</a> ({text(curr_findings[fid]["title"])})'
                if fid in curr_findings
                else f'<a href="#{text(fid)}">{text(fid)}</a>'
                for fid in item["after"]
            )
            or '<span class="muted">None</span>'
        )
        before_codes = (
            ", ".join(
                f"<code>{text(fid)}</code> ({text(prior_findings[fid]['title'])})"
                if fid in prior_findings
                else f"<code>{text(fid)}</code>"
                for fid in item["before"]
            )
            or '<span class="muted">None</span>'
        )
        topic_html, loc_html = _collision_summary_html(
            item["before"],
            item["after"],
            prior_findings,
            curr_findings,
            prior_evidence,
            curr_evidence,
        )
        ambiguous_rows_list.append(
            f"<tr><td>{after_links}</td><td>{before_codes}</td><td>{topic_html}</td><td>{loc_html}</td></tr>"
        )
    ambiguous_rows = (
        "".join(ambiguous_rows_list)
        or '<tr><td colspan="4" class="empty-state">No ambiguous fingerprint collisions.</td></tr>'
    )
    caveats = diff.get("limitations", [])
    callout_html = (
        '<div class="callout" role="note">'
        + "".join(f"<p>{text(note)}</p>" for note in caveats)
        + "</div>"
        if caveats
        else ""
    )
    prior_run = text(prior.get("run_id", "prior"))
    curr_run = text(audit["run_id"])
    return (
        f"<p>Compared prior run <code>{prior_run}</code> against <code>{curr_run}</code>: "
        f"{len(diff['introduced'])} introduced, {len(diff['status_changed'])} status changed, "
        f"{len(diff['persisting'])} persisting, {len(diff['not_observed'])} not observed, "
        f"{len(diff['ambiguous'])} ambiguous.</p>"
        f"{callout_html}"
        f"<h3>Introduced ({len(diff['introduced'])})</h3>"
        f'<div class="table-container"><table><thead><tr><th>Finding</th><th>Severity</th><th>Evidence status</th><th>Title</th></tr></thead><tbody>{introduced_rows}</tbody></table></div>'
        f"<h3>Status changed ({len(diff['status_changed'])})</h3>"
        f'<div class="table-container"><table><thead><tr><th>Current finding</th><th>Prior ID</th><th>Severity</th><th>Status transition</th><th>Title</th></tr></thead><tbody>{changed_rows}</tbody></table></div>'
        f"<h3>Persisting ({len(diff['persisting'])})</h3>"
        f'<div class="table-container"><table><thead><tr><th>Current finding</th><th>Prior ID</th><th>Severity</th><th>Evidence status</th><th>Title</th></tr></thead><tbody>{persisting_rows}</tbody></table></div>'
        f"<h3>Not observed in current run ({len(diff['not_observed'])})</h3>"
        f'<div class="table-container"><table><thead><tr><th>Prior ID</th><th>Prior severity</th><th>Prior status</th><th>Title</th></tr></thead><tbody>{absent_rows}</tbody></table></div>'
        f"<h3>Ambiguous collisions ({len(diff['ambiguous'])})</h3>"
        f'<div class="table-container"><table><thead><tr><th>Current findings</th><th>Prior IDs</th><th>Topic</th><th>Collision location (path · symbol)</th></tr></thead><tbody>{ambiguous_rows}</tbody></table></div>'
    )


def _render_audit_html(
    audit: dict,
    prior: dict | None = None,
    remediation: dict | None = None,
) -> str:
    errors = audit_errors(audit)
    if errors:
        raise ValueError("Invalid audit ledger: " + "; ".join(errors[:5]))
    latest_remediation, has_remediation = _index_remediation(remediation, audit)
    evidence = {e["id"]: e for e in audit["evidence"]}
    findings = sorted(
        audit["findings"],
        key=lambda f: (
            _SEVERITY_ORDER.index(f["severity"]),
            f["id"],
        ),
    )
    findings_by_id = {f["id"]: f for f in findings}
    cards = []
    for f in findings:
        locations = "".join(
            f"<li><code>{text(evidence[eid]['path'])}:{evidence[eid]['line_start']}–{evidence[eid]['line_end']}</code> — {text(evidence[eid]['symbol'])} — {text(evidence[eid]['context'])}</li>"
            for eid in f["evidence_ids"]
        )
        locations_html = f"<ul>{locations}</ul>" if locations else ""
        sequence = "".join(f"<li>{text(step)}</li>" for step in f["sequence"])
        criteria = "".join(f"<li>{text(step)}</li>" for step in f["acceptance"])
        contracts_html = (
            f"<p>Contracts: {text(', '.join(f['contract_ids']))}</p>"
            if f["contract_ids"]
            else ""
        )
        commands_html = (
            f"<pre>{text(chr(10).join(f['validation']['commands']))}</pre>"
            if f["validation"]["commands"]
            else ""
        )
        if f["related_findings"]:
            related_links = ", ".join(
                f'<a href="#{text(rf)}">{text(rf)}</a>'
                if rf in findings_by_id
                else text(rf)
                for rf in f["related_findings"]
            )
            related_html = f"<p>Related findings: {related_links}</p>"
        else:
            related_html = ""
        rem_entry = latest_remediation.get(f["id"])
        rem_attr = rem_entry["state"] if rem_entry else "unreconciled"
        if rem_entry:
            rem_state = rem_entry["state"]
            rem_label = _REMEDIATION_LABELS[rem_state]
            rem_tone = _REMEDIATION_TONES[rem_state]
            rem_badge_html = f' <span class="badge badge-{rem_tone} {rem_tone}">{text(rem_label)}</span>'
            rem_note_html = (
                f'<p class="remediation-note"><b>Remediation ({text(rem_label)}):</b> {text(rem_entry["note"])}</p>'
                if rem_entry["note"]
                else ""
            )
        else:
            rem_badge_html = ""
            rem_note_html = ""
        cards.append(f'''<article class="finding" data-severity="{text(f["severity"])}" data-status="{text(f["status"])}" data-remediation="{text(rem_attr)}" id="{text(f["id"])}"><h3>{text(f["id"])}: {text(f["title"])}</h3>
<p class="badges"><span class="badge badge-{text(f["severity"])} {text(f["severity"])}">{text(f["severity"])} · {text(f["status"])} · confidence {text(f["confidence"])}</span>{rem_badge_html}</p>
{rem_note_html}<p>{text(f["impact"])}</p><details><summary>Developer: failure, source, and correction</summary><p>{text(f["preconditions"])}</p><ol class="trace">{sequence}</ol><p><b>Expected:</b> {text(f["expected"])}</p><p><b>Actual:</b> {text(f["actual"])}</p>{locations_html}<p><b>Cause:</b> {text(f["root_cause"])}</p><p><b>Proposed remedy:</b> {text(f["remedy"])}</p></details>
<details><summary>Reviewer: contracts, safeguards, and verification</summary>{contracts_html}<p>{text(f["counterevidence"])}</p><p>Validation: {text(f["validation"]["state"])}</p>{commands_html}<p>{text(f["validation"]["result"])}</p></details>
<details><summary>Implementation: acceptance and relationships</summary><ul>{criteria}</ul>{related_html}<p class="muted">Evidence certainty is separate from remediation state.</p></details></article>''')
    coverage = (
        "".join(
            f"<tr><td>{text(c['topic'])}</td><td>{text(c['scope'])}</td><td>{text(c['status'])}</td><td>{text(c['notes'])}</td><td>{text(c['next_action'])}</td></tr>"
            for c in audit["coverage"]
        )
        or '<tr><td colspan="5" class="empty-state">No coverage recorded.</td></tr>'
    )
    limitations_html = (
        "<ul>"
        + "".join(f"<li>{text(item)}</li>" for item in audit["limitations"])
        + "</ul>"
        if audit["limitations"]
        else '<p class="muted">No explicit limitations recorded.</p>'
    )
    checks = (
        "".join(
            f"<tr><td>{text(c['name'])}</td><td>{text(c['state'])}</td><td><code>{text(c['command'])}</code></td><td>{text(c['result'])}</td></tr>"
            for c in audit["checks"]
        )
        or '<tr><td colspan="4" class="empty-state">No checks recorded.</td></tr>'
    )
    comparison_html = _render_comparison_html(prior, audit)
    if len(findings) > 0:
        severity_options = "".join(
            f'<option value="{s}">{text(_SEVERITY_LABELS[s])} ({sum(f["severity"] == s for f in findings)})</option>'
            for s in _SEVERITY_ORDER
        )
        status_options = "".join(
            f'<option value="{s}">{text(_STATUS_LABELS[s])} ({sum(f["status"] == s for f in findings)})</option>'
            for s in _STATUS_ORDER
        )
        if has_remediation:
            rem_opts = [
                f'<option value="{st}">{text(_REMEDIATION_LABELS[st])} ({sum(latest_remediation.get(f["id"], {}).get("state") == st for f in findings)})</option>'
                for st in _REMEDIATION_ORDER
            ]
            unreconciled_count = sum(
                f["id"] not in latest_remediation for f in findings
            )
            if unreconciled_count > 0:
                rem_opts.append(
                    f'<option value="unreconciled">Unreconciled ({unreconciled_count})</option>'
                )
            remediation_filter_html = (
                f'<label for="remediation">Remediation state <select id="remediation">'
                f'<option value="">All remediation states ({len(findings)})</option>'
                f"{''.join(rem_opts)}</select></label>"
            )
        else:
            remediation_filter_html = ""
        findings_body = (
            f'<label for="query">Search findings <input id="query" type="search"></label>'
            f'<label for="severity">Severity <select id="severity"><option value="">All severities ({len(findings)})</option>{severity_options}</select></label>'
            f'<label for="status">Evidence status <select id="status"><option value="">All statuses ({len(findings)})</option>{status_options}</select></label>'
            f"{remediation_filter_html}"
            f'<button id="toggle-details" type="button" aria-expanded="false">Expand all</button>'
            f'<p id="count" role="status" aria-live="polite"></p>'
            f'<div id="empty-state" class="empty-state" hidden><p>No findings match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            f"{''.join(cards)}"
        )
    else:
        findings_body = (
            "<p>No findings recorded. Review coverage before drawing conclusions.</p>"
        )
    record_label = (
        "1 finding record" if len(findings) == 1 else f"{len(findings)} finding records"
    )
    sev_summary = ", ".join(
        f"{sum(f['severity'] == s for f in findings)} {s}" for s in _SEVERITY_ORDER
    )
    if has_remediation:
        vf_count = sum(
            latest_remediation.get(f["id"], {}).get("state") == "verified-fixed"
            for f in findings
        )
        rem_parts = [f"{vf_count} verified fixed"]
        for st in _REMEDIATION_ORDER:
            if st == "verified-fixed":
                continue
            st_count = sum(
                latest_remediation.get(f["id"], {}).get("state") == st for f in findings
            )
            if st_count > 0:
                rem_parts.append(f"{st_count} {_REMEDIATION_LABELS[st].lower()}")
        unrec_exec = sum(f["id"] not in latest_remediation for f in findings)
        if unrec_exec > 0:
            rem_parts.append(f"{unrec_exec} unreconciled")
        rem_exec_text = ", ".join(rem_parts) + "."
    else:
        rem_exec_text = "0 verified fixed (no remediation records supplied)."
    body = f"""<header><p class="muted">fred-skills · local evidence report</p><h1>Repository audit</h1><p>{text(audit["run_id"])} · {text(audit["repository"]["identity"])}</p><p>Revision: <code>{text(audit["repository"]["revision"])}</code> · dirty: {text(audit["repository"]["dirty"])}</p><nav aria-label="Report views"><a href="#executive">Executive</a><a href="#findings">Findings</a><a href="#coverage">Coverage</a><a href="#checks">Checks</a><a href="#comparison">Comparison</a></nav></header>
<section id="executive"><h2>Executive view</h2><p>{record_label}; {sum(f["status"] == "confirmed" for f in findings)} reproduced; {sum(f["status"] == "refuted" for f in findings)} refuted. These counts are not a repository safety score.</p><p><b>Severity counts:</b> {sev_summary}.</p><p><b>Remediation totals:</b> {rem_exec_text}</p>{limitations_html}</section>
<section id="findings"><h2>Review findings</h2>{findings_body}</section>
<section id="coverage"><h2>Coverage and continuation</h2><div class="table-container"><table><thead><tr><th>Topic</th><th>Scope</th><th>Status</th><th>Notes</th><th>Next action</th></tr></thead><tbody>{coverage}</tbody></table></div></section>
<section id="checks"><h2>Executed and unexecuted checks</h2><div class="table-container"><table><thead><tr><th>Check</th><th>State</th><th>Command</th><th>Result</th></tr></thead><tbody>{checks}</tbody></table></div></section><section id="comparison"><h2>Comparison</h2>{comparison_html}</section>"""
    return _page_shell(f"Repository audit — {text(audit['run_id'])}", body)


def render(
    audit: dict,
    output: Path,
    prior: dict | None = None,
    remediation: dict | None = None,
) -> Path:
    if not isinstance(audit, dict):
        raise FredCliError(
            f"Expected JSON object for report input, got {type(audit).__name__}",
            code="invalid_json_root",
            hint="Pass a valid 'audit.json' or suite deliverable JSON object.",
        )
    domain_kind = _detect_domain_kind(audit)
    if domain_kind is not None:
        if prior is not None:
            raise FredCliError(
                f"Prior comparison ('--prior') requires 'audit.json' ledgers; domain artifact '{domain_kind}.json' is not supported",
                code="unsupported_domain_artifact",
                hint="Omit --prior when rendering a domain report, or pass 'audit.json' ledgers.",
            )
        if remediation is not None:
            raise FredCliError(
                f"Remediation records ('--remediation') require 'audit.json' ledgers; domain artifact '{domain_kind}.json' is not supported",
                code="unsupported_domain_artifact",
                hint="Omit --remediation when rendering a domain report, or pass an 'audit.json' ledger.",
            )
        _validate_domain_payload(audit, domain_kind)
        renderers = {
            "architecture": _render_architecture_html,
            "review": _render_review_html,
            "testing": _render_testing_html,
        }
        html = renderers[domain_kind](audit)
    else:
        html = _render_audit_html(audit, prior=prior, remediation=remediation)
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
        raise FredCliError(
            f"Unsupported issue format: {format}",
            code="invalid_issue_format",
            hint="Choose --format markdown or --format json.",
        )
    if not isinstance(audit, dict):
        raise FredCliError(
            "Invalid ledger; cannot draft issues",
            code="invalid_audit_ledger",
            hint="Pass a valid 'audit.json' ledger object.",
        )
    domain_kind = _detect_domain_kind(audit)
    if domain_kind is not None:
        raise FredCliError(
            f"Issue drafts ('issues') require an 'audit.json' ledger; domain artifact '{domain_kind}.json' is not supported",
            code="unsupported_domain_artifact",
            hint="Pass an 'audit.json' ledger from a repository-audits run.",
        )
    errors = audit_errors(audit)
    if errors:
        raise FredCliError(
            "Invalid ledger; cannot draft issues",
            code="invalid_audit_ledger",
            hint="Run 'python scripts/fred.py validate-run <dir>' to inspect ledger schema errors.",
        )
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise FredCliError(
            "Issue output directory must be empty; stale drafts will not be mixed with current results",
            code="non_empty_output_directory",
            hint="Choose a new or empty --output directory for generated issue drafts.",
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
