from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from pathlib import Path
from typing import Any

from .common import (
    DOMAIN_DELIVERABLE_SCHEMAS,
    FredCliError,
    _detect_domain_kind,
    _validate_canonical_artifact,
)
from .runs import audit_errors, compare, fingerprint

STYLE = """:root{--table-border:#ddd}body{font:16px/1.6 system-ui,sans-serif;margin:auto;max-width:1150px;padding:2rem;color:#172635;background:#f5f7fa}main{display:block}header,section,article{background:white;border:1px solid #dce4eb;border-radius:12px;padding:1.2rem;margin:1rem 0}h1,h2,h3{line-height:1.2}h1,h2,h3,p,li,summary{overflow-wrap:anywhere}nav{display:flex;gap:.45rem .9rem;flex-wrap:wrap}a{color:#1556a5}pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em;white-space:pre-wrap;overflow-wrap:anywhere}pre{background:#edf2f7;border:1px solid #dce4eb;border-radius:8px;padding:.85rem 1rem;margin:.75rem 0;overflow-x:auto;box-sizing:border-box;max-width:100%}code{background:#edf2f7;padding:.12rem .35rem;border-radius:4px}pre code{background:none;border:0;padding:0;border-radius:0;font-size:inherit}table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;border-bottom:1px solid var(--table-border);padding:.6rem}.table-container{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}.badges{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}.badge{display:inline-block;padding:.2rem .6rem;border-radius:999px;font-size:.85rem;font-weight:650;box-sizing:border-box;max-width:100%}.badge-critical,.critical{background:#fed7d7;color:#9b1c1c}.badge-high,.high{background:#feebc8;color:#7b341e}.badge-medium,.medium{background:#fefcbf;color:#744210}.badge-low,.low{background:#c6f6d5;color:#22543d}.badge-info,.info,.badge-advisory,.advisory{background:#bee3f8;color:#2a4365}.callout{border-left:4px solid #5380ab;background:#edf2f7;padding:.75rem 1rem;border-radius:6px;margin:.8rem 0}.callout p{margin:.25rem 0}.empty-state{text-align:center;padding:1.5rem;color:#53667a}.muted{color:#53667a}input,select,button{font:inherit;padding:.5rem;border:1px solid #9baec1;border-radius:5px;box-sizing:border-box;max-width:100%}button{background:#fff;cursor:pointer}label{display:inline-block;margin:.4rem;box-sizing:border-box;max-width:100%}#toggle-details{margin:.4rem}[hidden]{display:none}.trace{border-left:3px solid #5380ab;padding-left:1rem}:focus-visible{outline:3px solid #1556a5}@media (max-width: 640px){body{padding:.75rem}header,section,article{padding:.85rem;margin:.75rem 0}header h1{font-size:1.35rem;margin:.2rem 0}header p{margin:.35rem 0}nav{gap:.35rem .7rem;font-size:.92rem}label{display:block;margin:.4rem 0}input,select{width:100%}#toggle-details{margin:.4rem 0}}@media(prefers-color-scheme:dark){:root{--table-border:#405164}body{background:#101821;color:#e3ecf5}header,section,article{background:#182531;border-color:#405164}a{color:#95c7ff}pre{background:#101821;border-color:#405164}code{background:#1e2f3f}pre code{background:none}.muted{color:#b2c6d9}.empty-state{color:#b2c6d9}.callout{background:#1e2f3f;border-left-color:#95c7ff}input,select,button{background:#101821;color:#e3ecf5;border-color:#405164}:focus-visible{outline:3px solid #95c7ff}.badge-critical,.critical{background:#742a2a;color:#feb2b2}.badge-high,.high{background:#7b341e;color:#fbd38d}.badge-medium,.medium{background:#744210;color:#faf089}.badge-low,.low{background:#22543d;color:#9ae6b4}.badge-info,.info,.badge-advisory,.advisory{background:#2a4365;color:#90cdf4}}@media print{body{background:#fff;color:#172635;padding:0;max-width:none}header,section,article{background:#fff;color:#172635;border-color:#dce4eb}pre{background:#f5f7fa;color:#172635;border-color:#dce4eb}code{background:#f5f7fa;color:#172635}pre code{background:none}.callout{background:#edf2f7;color:#172635;border-left-color:#5380ab}label[for="query"],label[for="severity"],label[for="status"],label[for="remediation"],#query,#severity,#status,#remediation,#toggle-details,#empty-state,#reset-filters{display:none!important}}"""
SCRIPT = """const q=document.querySelector('#query'),sev=document.querySelector('#severity'),s=document.querySelector('#status'),rem=document.querySelector('#remediation'),e=document.querySelector('#empty-state'),r=document.querySelector('#reset-filters'),t=document.querySelector('#toggle-details');function visibleDetails(){return[...document.querySelectorAll('article.finding:not([hidden]) details')];}function syncToggle(){if(!t)return;const v=visibleDetails(),x=v.length>0&&v.every(d=>d.open);t.setAttribute('aria-expanded',x?'true':'false');t.textContent=x?'Collapse all':'Expand all';}function filter(){const qv=q?q.value.toLowerCase():'',toks=qv.split(/[\\s-]+/).filter(Boolean),sevv=sev?sev.value:'',sv=s?s.value:'',remv=rem?rem.value:'',tasks=document.querySelectorAll('details.task'),ms=new Set();let tn=0,n=0;for(const el of tasks){const suites=(el.dataset.suites||'').split(' ').filter(Boolean),fOk=!sv||suites.includes(sv),hay=(el.dataset.search||el.textContent||'').toLowerCase(),qOk=toks.every(k=>hay.includes(k));if(toks.length&&qOk){for(const sk of(el.dataset.skills||'').split(' '))if(sk)ms.add(sk);}el.hidden=!(fOk&&qOk);if(!el.hidden)tn++;}const a=document.querySelectorAll('article.finding');for(const el of a){const fOk=(!sevv||el.dataset.severity===sevv)&&(!sv||el.dataset.status===sv)&&(!remv||el.dataset.remediation===remv),txt=el.textContent.toLowerCase(),qOk=!toks.length||ms.has(el.id)||toks.every(k=>txt.includes(k));el.hidden=!(fOk&&qOk);if(!el.hidden)n++;for(const d of el.querySelectorAll('details')){if(toks.length){if(!('preSearchOpen' in d.dataset))d.dataset.preSearchOpen=d.open?'1':'0';const dt=d.textContent.toLowerCase();if(!('prePrintOpen' in d.dataset))d.open=d.dataset.preSearchOpen==='1'||(!el.hidden&&toks.every(k=>dt.includes(k)));}else if('preSearchOpen' in d.dataset){const p=d.dataset.preSearchOpen==='1';delete d.dataset.preSearchOpen;if(!('prePrintOpen' in d.dataset))d.open=p;else d.dataset.prePrintOpen=p?'1':'0';}}}for(const sec of document.querySelectorAll('section[data-filter-section]')){sec.hidden=!sec.querySelector('details.task:not([hidden]),article.finding:not([hidden])');}const c=document.querySelector('#count');if(c){const base=c.dataset.noun||(tasks.length?'skills':'findings'),noun=n===1?base.replace(/s$/,''):base;c.textContent=tasks.length?tn+' '+(tn===1?'task':'tasks')+', '+n+' '+noun+' shown':n+' '+noun+' shown';}if(e)e.hidden=!((a.length+tasks.length)>0&&n===0&&tn===0);syncToggle();}function openPrint(){for(const d of document.querySelectorAll('details')){if(!('prePrintOpen' in d.dataset))d.dataset.prePrintOpen=d.open?'1':'0';d.open=true;}syncToggle();}function restorePrint(){for(const d of document.querySelectorAll('details')){if('prePrintOpen' in d.dataset){d.open=d.dataset.prePrintOpen==='1';delete d.dataset.prePrintOpen;}}syncToggle();}function reveal(raw){if(!raw)return;let id=raw;try{id=decodeURIComponent(raw);}catch{}const el=document.getElementById(id);if(!el||!el.classList.contains('finding'))return;if(el.hidden){if(sev&&sev.value&&el.dataset.severity!==sev.value)sev.value='';if(s&&s.value&&el.dataset.status!==s.value)s.value='';if(rem&&rem.value&&el.dataset.remediation!==rem.value)rem.value='';filter();if(el.hidden&&q&&q.value){q.value='';filter();}}el.scrollIntoView();}function revealHash(){if(location.hash.length>1)reveal(location.hash.slice(1));}document.addEventListener('click',ev=>{const tgt=ev.target instanceof Element?ev.target:null;if(tgt&&tgt.closest('summary'))queueMicrotask(syncToggle);const link=tgt?tgt.closest('a[href^="#"]'):null;if(link){const h=link.getAttribute('href')||'';if(h.length>1)reveal(h.slice(1));}});document.addEventListener('toggle',syncToggle,true);window.addEventListener('hashchange',revealHash);window.addEventListener('beforeprint',openPrint);window.addEventListener('afterprint',restorePrint);const mq=window.matchMedia?window.matchMedia('print'):null;if(mq&&mq.addEventListener)mq.addEventListener('change',ev=>ev.matches?openPrint():restorePrint());if(q)q.addEventListener('input',filter);if(sev)sev.addEventListener('change',filter);if(s)s.addEventListener('change',filter);if(rem)rem.addEventListener('change',filter);if(r)r.addEventListener('click',()=>{if(q)q.value='';if(sev)sev.value='';if(s)s.value='';if(rem)rem.value='';filter();if(q)q.focus();});if(t)t.addEventListener('click',()=>{const v=visibleDetails(),x=!(v.length>0&&v.every(d=>d.open));for(const d of v){d.open=x;if('preSearchOpen' in d.dataset)d.dataset.preSearchOpen=x?'1':'0';}syncToggle();});filter();revealHash();if(mq&&mq.matches)openPrint();"""

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


def _csp_value(include_script: bool = True) -> str:
    if include_script:
        script_hash = base64.b64encode(
            hashlib.sha256(SCRIPT.encode()).digest()
        ).decode()
        script_src = f"script-src 'sha256-{script_hash}'"
    else:
        script_src = "script-src 'none'"
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; "
        f"{script_src}; base-uri 'none'; "
        f"form-action 'none'; connect-src 'none'"
    )


def _page_shell(title: str, body_html: str, include_script: bool = True) -> str:
    csp = _csp_value(include_script=include_script)
    if "<main" in body_html:
        wrapped_body = body_html
    elif "</header>" in body_html:
        header_part, main_part = body_html.split("</header>", 1)
        wrapped_body = f"{header_part}</header><main>{main_part}</main>"
    else:
        wrapped_body = f"<main>{body_html}</main>"
    script_html = f"<script>{SCRIPT}</script>" if include_script else ""
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}">'
        f"<title>{title}</title><style>{STYLE}</style></head><body>\n"
        f"{wrapped_body}{script_html}</body></html>"
    )


def _validate_domain_payload(payload: dict, kind: str) -> None:
    filename = f"{kind}.json"
    schema_rel = DOMAIN_DELIVERABLE_SCHEMAS[filename]
    errors = _validate_canonical_artifact(
        payload,
        artifact_name=filename,
        schema_rel=schema_rel,
    )
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
    components = doc.get("components", [])
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
    component_rows = (
        "".join(
            f"<tr><td><code>{text(c['id'])}</code></td>"
            f"<td>{text(c['name'])}</td>"
            f"<td>{text(c['responsibility'])}</td>"
            f"<td>{', '.join(f'<code>{text(p)}</code>' for p in c.get('paths', [])) or '<span class="muted">None</span>'}</td>"
            f"<td>{', '.join(f'<code>{text(dep)}</code>' for dep in c.get('depends_on', [])) or '<span class="muted">None</span>'}</td></tr>"
            for c in components
        )
        or '<tr><td colspan="5" class="empty-state">No components recorded.</td></tr>'
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
        f'<nav aria-label="Report views"><a href="#executive">Overview</a><a href="#boundaries">Boundaries</a><a href="#components">Components</a><a href="#decisions">Decisions</a></nav></header>\n'
        f'<section id="executive"><h2>Architecture overview</h2><p>{len(boundaries)} system boundaries; {len(components)} components; {len(decisions)} decision records; status: <code>{text(doc["status"])}</code>.</p></section>\n'
        f'<section id="boundaries"><h2>System boundaries</h2><div class="table-container"><table><thead><tr><th>Boundary</th><th>Type</th><th>Description</th><th>Invariants</th></tr></thead><tbody>{boundary_rows}</tbody></table></div></section>\n'
        f'<section id="components"><h2>Component decomposition</h2><div class="table-container"><table><thead><tr><th>ID</th><th>Component</th><th>Responsibility</th><th>Paths</th><th>Depends on</th></tr></thead><tbody>{component_rows}</tbody></table></div></section>\n'
        f'<section id="decisions"><h2>Architectural decisions</h2>{decisions_body}</section>'
    )
    return _page_shell(f"Software architecture — {text(doc['title'])}", body)


def _render_spec_html(doc: dict) -> str:
    status_badges = {
        "converged": "low",
        "approved": "low",
        "interviewing": "info",
        "draft": "info",
        "diverged": "high",
    }
    status_tone = status_badges.get(doc["status"], "info")
    decisions = doc.get("interview_decisions", [])
    scopes = doc.get("new_file_scopes", [])
    non_goals = doc.get("out_of_scope", [])
    commands = doc.get("verification_commands", [])
    checks = doc.get("convergence_checks", [])
    scope_list = (
        "<ul>" + "".join(f"<li><code>{text(s)}</code></li>" for s in scopes) + "</ul>"
        if scopes
        else '<p class="muted">No approved file scopes recorded.</p>'
    )
    non_goal_list = (
        "<ul>" + "".join(f"<li>{text(ng)}</li>" for ng in non_goals) + "</ul>"
        if non_goals
        else '<p class="muted">No out-of-scope exclusions recorded.</p>'
    )
    cmd_list = (
        "<ul>"
        + "".join(f"<li><code>{text(cmd)}</code></li>" for cmd in commands)
        + "</ul>"
        if commands
        else '<p class="muted">No verification commands recorded.</p>'
    )
    if decisions:
        cards = [
            f'<article class="finding" data-status="{text(doc["status"])}" id="{text(d["id"])}">'
            f"<h3>{text(d['id'])} ({text(d['category'])}): {text(d['question'])}</h3>"
            f"<p><b>Decision:</b> {text(d['decision'])}</p>"
            + (
                f"<details><summary>Rationale</summary><p>{text(d['rationale'])}</p></details>"
                if d.get("rationale")
                else ""
            )
            + "</article>"
            for d in decisions
        ]
        decisions_body = (
            '<label for="query">Search decisions <input id="query" type="search"></label>'
            '<button id="toggle-details" type="button" aria-expanded="false">Expand all</button>'
            '<p id="count" data-noun="decisions" role="status" aria-live="polite"></p>'
            '<div id="empty-state" class="empty-state" hidden><p>No decisions match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
            + "".join(cards)
        )
    else:
        decisions_body = "<p>No interview decisions recorded.</p>"
    check_rows = (
        "".join(
            f"<tr><td>{text(chk['id'])}</td><td><code>{text(chk['target'])}</code></td>"
            f'<td><span class="badge badge-info info">{text(chk["status"])}</span></td>'
            f"<td>{text(chk.get('evidence', ''))}</td></tr>"
            for chk in checks
        )
        or '<tr><td colspan="4" class="empty-state">No convergence checks recorded.</td></tr>'
    )
    body = (
        f'<header><p class="muted">fred-skills · spec-driven development report</p>'
        f"<h1>{text(doc['title'])}</h1>"
        f'<p><span class="badge badge-{status_tone} {status_tone}">{text(doc["status"])}</span> · schema {text(doc["schema_version"])}</p>'
        f'<nav aria-label="Report views"><a href="#executive">Overview</a><a href="#decisions">Decisions</a><a href="#scopes">Scopes</a><a href="#convergence">Convergence</a></nav></header>\n'
        f'<section id="executive"><h2>Specification overview</h2><p>{len(decisions)} interview decisions; {len(scopes)} file scopes; {len(commands)} verification commands; status: <code>{text(doc["status"])}</code>.</p></section>\n'
        f'<section id="decisions"><h2>Interview decisions</h2>{decisions_body}</section>\n'
        f'<section id="scopes"><h2>Scopes and verification</h2><h3>Approved file scopes</h3>{scope_list}<h3>Out of scope</h3>{non_goal_list}<h3>Verification commands</h3>{cmd_list}</section>\n'
        f'<section id="convergence"><h2>Convergence checks</h2><div class="table-container"><table><thead><tr><th>ID</th><th>Target</th><th>Status</th><th>Evidence</th></tr></thead><tbody>{check_rows}</tbody></table></div></section>'
    )
    return _page_shell(f"Specification — {text(doc['title'])}", body)


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


def _validate_reconciliation_schema(remediation: dict[str, Any]) -> None:
    if (
        "finding_fingerprint_matches" not in remediation
        and "unmapped_records" not in remediation
    ):
        return
    rem_errors = _validate_canonical_artifact(
        remediation, artifact_name="reconciliation.json"
    )
    if rem_errors:
        raise ValueError("Invalid remediation ledger: " + "; ".join(rem_errors[:5]))


def _validate_remediation_structure(remediation: Any) -> None:
    mapping = remediation if isinstance(remediation, dict) else {}
    has_records = "records" in mapping
    has_transitions = "remediation_state_transitions" in mapping
    if not has_records and not has_transitions:
        raise ValueError(
            "Invalid remediation ledger: expected 'records' or 'remediation_state_transitions'"
        )
    if has_records and not isinstance(mapping["records"], list):
        raise ValueError("Invalid remediation ledger: expected a 'records' list")
    if has_transitions and not isinstance(
        mapping["remediation_state_transitions"], list
    ):
        raise ValueError(
            "Invalid remediation ledger: expected a 'remediation_state_transitions' list"
        )
    _validate_reconciliation_schema(mapping)


def _is_unambiguous_fingerprint_match(match: object) -> bool:
    if not isinstance(match, dict) or match.get("match_status") == "ambiguous":
        return False
    return isinstance(match.get("finding_id"), str) and isinstance(
        match.get("fingerprint"), str
    )


def _build_explicit_fingerprint_map(remediation: dict[str, Any]) -> dict[str, str]:
    return {
        match["finding_id"]: match["fingerprint"]
        for match in (remediation.get("finding_fingerprint_matches") or [])
        if _is_unambiguous_fingerprint_match(match)
    }


def _match_remediation_finding_id(
    fid: object,
    fp: object,
    finding_ids: set[str],
    fp_to_ids: dict[str, list[str]],
) -> str | None:
    if isinstance(fid, str) and fid in finding_ids:
        return fid
    if isinstance(fp, str) and len(fp_to_ids.get(fp, [])) == 1:
        return fp_to_ids[fp][0]
    return None


def _apply_remediation_transition(
    tr: object,
    finding_ids: set[str],
    fp_to_ids: dict[str, list[str]],
    explicit_fp_map: dict[str, str],
    latest: dict[str, dict[str, str]],
) -> None:
    state = tr.get("new_state") if isinstance(tr, dict) else None
    if not isinstance(tr, dict) or state not in _REMEDIATION_LABELS:
        raise ValueError(f"Unknown remediation state: {state!r}")
    fid = tr.get("finding_id")
    fp = tr.get("fingerprint") or (
        explicit_fp_map.get(fid) if isinstance(fid, str) else None
    )
    matched_id = _match_remediation_finding_id(fid, fp, finding_ids, fp_to_ids)
    if matched_id is not None:
        latest[matched_id] = {"state": state, "note": str(tr.get("reason", ""))}


def _apply_remediation_record(
    rec: object,
    finding_ids: set[str],
    fp_to_ids: dict[str, list[str]],
    latest: dict[str, dict[str, str]],
) -> None:
    state = rec.get("state") if isinstance(rec, dict) else None
    if not isinstance(rec, dict) or state not in _REMEDIATION_LABELS:
        raise ValueError(f"Unknown remediation state: {state!r}")
    matched_id = _match_remediation_finding_id(
        rec.get("finding_id"), rec.get("fingerprint"), finding_ids, fp_to_ids
    )
    if matched_id is not None:
        latest[matched_id] = {"state": state, "note": str(rec.get("note", ""))}


def _index_remediation(
    remediation: dict[str, Any] | None, audit: dict[str, Any]
) -> tuple[dict[str, dict[str, str]], bool]:
    if remediation is None:
        return {}, False
    _validate_remediation_structure(remediation)
    raw_records = remediation.get("records") or []
    raw_transitions = remediation.get("remediation_state_transitions") or []
    finding_ids = {f["id"] for f in audit["findings"]}
    fp_to_ids: dict[str, list[str]] = {}
    for finding in audit["findings"]:
        fp_to_ids.setdefault(fingerprint(finding, audit), []).append(finding["id"])
    explicit_fp_map = _build_explicit_fingerprint_map(remediation)
    latest: dict[str, dict[str, str]] = {}
    for tr in raw_transitions:
        _apply_remediation_transition(
            tr, finding_ids, fp_to_ids, explicit_fp_map, latest
        )
    for rec in raw_records:
        _apply_remediation_record(rec, finding_ids, fp_to_ids, latest)
    return latest, (len(raw_records) + len(raw_transitions)) > 0


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


def _finding_summary_cells(
    fid: str, findings_map: dict[str, dict[str, Any]]
) -> tuple[str, str, str]:
    finding = findings_map.get(fid)
    if finding is None:
        return "—", "—", "—"
    sev_html = _severity_badge_html(finding["severity"])
    status_str = text(_STATUS_LABELS.get(finding["status"], finding["status"]))
    return sev_html, status_str, text(finding["title"])


def _render_single_finding_diff_rows(
    fids: list[str],
    findings_map: dict[str, dict[str, Any]],
    *,
    link: bool,
    empty_label: str,
) -> str:
    if not fids:
        return f'<tr><td colspan="4" class="empty-state">{empty_label}</td></tr>'
    rows: list[str] = []
    for fid in fids:
        id_cell = (
            f'<a href="#{text(fid)}">{text(fid)}</a>'
            if link
            else f"<code>{text(fid)}</code>"
        )
        sev_html, status_html, title_html = _finding_summary_cells(fid, findings_map)
        rows.append(
            f"<tr><td>{id_cell}</td><td>{sev_html}</td><td>{status_html}</td><td>{title_html}</td></tr>"
        )
    return "".join(rows)


def _render_paired_finding_diff_rows(
    items: list[dict[str, Any]],
    curr_findings: dict[str, dict[str, Any]],
    *,
    show_transition: bool,
    empty_label: str,
) -> str:
    if not items:
        return f'<tr><td colspan="5" class="empty-state">{empty_label}</td></tr>'
    rows: list[str] = []
    for item in items:
        after_id = item["after"]
        sev_html, status_html, title_html = _finding_summary_cells(
            after_id, curr_findings
        )
        if show_transition:
            status_cell = f"{text(_STATUS_LABELS.get(item['from'], item['from']))} → {text(_STATUS_LABELS.get(item['to'], item['to']))}"
        else:
            status_cell = status_html
        rows.append(
            f'<tr><td><a href="#{text(after_id)}">{text(after_id)}</a></td>'
            f"<td><code>{text(item['before'])}</code></td>"
            f"<td>{sev_html}</td><td>{status_cell}</td><td>{title_html}</td></tr>"
        )
    return "".join(rows)


def _format_collision_refs(
    fids: list[str],
    findings_map: dict[str, dict[str, Any]],
    *,
    link: bool,
) -> str:
    if not fids:
        return '<span class="muted">None</span>'
    parts: list[str] = []
    for fid in fids:
        base = (
            f'<a href="#{text(fid)}">{text(fid)}</a>'
            if link
            else f"<code>{text(fid)}</code>"
        )
        if fid in findings_map:
            parts.append(f"{base} ({text(findings_map[fid]['title'])})")
        else:
            parts.append(base)
    return ", ".join(parts)


def _render_ambiguous_diff_rows(
    ambiguous: list[dict[str, Any]],
    prior_findings: dict[str, dict[str, Any]],
    curr_findings: dict[str, dict[str, Any]],
    prior_evidence: dict[str, dict[str, Any]],
    curr_evidence: dict[str, dict[str, Any]],
) -> str:
    if not ambiguous:
        return '<tr><td colspan="4" class="empty-state">No ambiguous fingerprint collisions.</td></tr>'
    rows: list[str] = []
    for item in ambiguous:
        after_links = _format_collision_refs(item["after"], curr_findings, link=True)
        before_codes = _format_collision_refs(
            item["before"], prior_findings, link=False
        )
        topic_html, loc_html = _collision_summary_html(
            item["before"],
            item["after"],
            prior_findings,
            curr_findings,
            prior_evidence,
            curr_evidence,
        )
        rows.append(
            f"<tr><td>{after_links}</td><td>{before_codes}</td><td>{topic_html}</td><td>{loc_html}</td></tr>"
        )
    return "".join(rows)


def _render_comparison_html(prior: dict[str, Any] | None, audit: dict[str, Any]) -> str:
    if prior is None:
        return "<p>No prior ledger supplied. Missing findings on another run do not prove resolution.</p>"
    diff = compare(prior, audit)
    prior_findings = {f["id"]: f for f in prior.get("findings", [])}
    curr_findings = {f["id"]: f for f in audit.get("findings", [])}
    prior_evidence = {e["id"]: e for e in prior.get("evidence", [])}
    curr_evidence = {e["id"]: e for e in audit.get("evidence", [])}

    introduced_rows = _render_single_finding_diff_rows(
        diff["introduced"],
        curr_findings,
        link=True,
        empty_label="No introduced findings.",
    )
    changed_rows = _render_paired_finding_diff_rows(
        diff["status_changed"],
        curr_findings,
        show_transition=True,
        empty_label="No status changes.",
    )
    persisting_rows = _render_paired_finding_diff_rows(
        diff["persisting"],
        curr_findings,
        show_transition=False,
        empty_label="No persisting findings.",
    )
    absent_rows = _render_single_finding_diff_rows(
        diff["not_observed"],
        prior_findings,
        link=False,
        empty_label="No absent prior findings.",
    )
    ambiguous_rows = _render_ambiguous_diff_rows(
        diff["ambiguous"],
        prior_findings,
        curr_findings,
        prior_evidence,
        curr_evidence,
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


def _render_remediation_badge_and_note(
    rem_entry: dict[str, str] | None,
) -> tuple[str, str, str]:
    if not rem_entry:
        return "unreconciled", "", ""
    rem_state = rem_entry["state"]
    rem_label = _REMEDIATION_LABELS[rem_state]
    rem_tone = _REMEDIATION_TONES[rem_state]
    rem_badge_html = (
        f' <span class="badge badge-{rem_tone} {rem_tone}">{text(rem_label)}</span>'
    )
    rem_note_html = (
        f'<p class="remediation-note"><b>Remediation ({text(rem_label)}):</b> {text(rem_entry["note"])}</p>'
        if rem_entry["note"]
        else ""
    )
    return rem_state, rem_badge_html, rem_note_html


def _render_related_findings_html(
    related_ids: list[str], findings_by_id: dict[str, dict[str, Any]]
) -> str:
    if not related_ids:
        return ""
    links = ", ".join(
        f'<a href="#{text(rf)}">{text(rf)}</a>' if rf in findings_by_id else text(rf)
        for rf in related_ids
    )
    return f"<p>Related findings: {links}</p>"


def _render_audit_finding_card(
    f: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    findings_by_id: dict[str, dict[str, Any]],
    rem_entry: dict[str, str] | None,
) -> str:
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
    related_html = _render_related_findings_html(f["related_findings"], findings_by_id)
    rem_attr, rem_badge_html, rem_note_html = _render_remediation_badge_and_note(
        rem_entry
    )
    return f'''<article class="finding" data-severity="{text(f["severity"])}" data-status="{text(f["status"])}" data-remediation="{text(rem_attr)}" id="{text(f["id"])}"><h3>{text(f["id"])}: {text(f["title"])}</h3>
<p class="badges"><span class="badge badge-{text(f["severity"])} {text(f["severity"])}">{text(f["severity"])} · {text(f["status"])} · confidence {text(f["confidence"])}</span>{rem_badge_html}</p>
{rem_note_html}<p>{text(f["impact"])}</p><details><summary>Developer: failure, source, and correction</summary><p>{text(f["preconditions"])}</p><ol class="trace">{sequence}</ol><p><b>Expected:</b> {text(f["expected"])}</p><p><b>Actual:</b> {text(f["actual"])}</p>{locations_html}<p><b>Cause:</b> {text(f["root_cause"])}</p><p><b>Proposed remedy:</b> {text(f["remedy"])}</p></details>
<details><summary>Reviewer: contracts, safeguards, and verification</summary>{contracts_html}<p>{text(f["counterevidence"])}</p><p>Validation: {text(f["validation"]["state"])}</p>{commands_html}<p>{text(f["validation"]["result"])}</p></details>
<details><summary>Implementation: acceptance and relationships</summary><ul>{criteria}</ul>{related_html}<p class="muted">Evidence certainty is separate from remediation state.</p></details></article>'''


def _render_remediation_filter_html(
    findings: list[dict[str, Any]],
    latest_remediation: dict[str, dict[str, str]],
    has_remediation: bool,
) -> str:
    if not has_remediation:
        return ""
    rem_opts = [
        f'<option value="{st}">{text(_REMEDIATION_LABELS[st])} ({sum(latest_remediation.get(f["id"], {}).get("state") == st for f in findings)})</option>'
        for st in _REMEDIATION_ORDER
    ]
    unreconciled_count = sum(f["id"] not in latest_remediation for f in findings)
    if unreconciled_count > 0:
        rem_opts.append(
            f'<option value="unreconciled">Unreconciled ({unreconciled_count})</option>'
        )
    return (
        f'<label for="remediation">Remediation state <select id="remediation">'
        f'<option value="">All remediation states ({len(findings)})</option>'
        f"{''.join(rem_opts)}</select></label>"
    )


def _render_audit_findings_section(
    findings: list[dict[str, Any]],
    cards: list[str],
    latest_remediation: dict[str, dict[str, str]],
    has_remediation: bool,
) -> str:
    if not findings:
        return (
            "<p>No findings recorded. Review coverage before drawing conclusions.</p>"
        )
    severity_options = "".join(
        f'<option value="{s}">{text(_SEVERITY_LABELS[s])} ({sum(f["severity"] == s for f in findings)})</option>'
        for s in _SEVERITY_ORDER
    )
    status_options = "".join(
        f'<option value="{s}">{text(_STATUS_LABELS[s])} ({sum(f["status"] == s for f in findings)})</option>'
        for s in _STATUS_ORDER
    )
    remediation_filter_html = _render_remediation_filter_html(
        findings, latest_remediation, has_remediation
    )
    return (
        f'<label for="query">Search findings <input id="query" type="search"></label>'
        f'<label for="severity">Severity <select id="severity"><option value="">All severities ({len(findings)})</option>{severity_options}</select></label>'
        f'<label for="status">Evidence status <select id="status"><option value="">All statuses ({len(findings)})</option>{status_options}</select></label>'
        f"{remediation_filter_html}"
        f'<button id="toggle-details" type="button" aria-expanded="false">Expand all</button>'
        f'<p id="count" role="status" aria-live="polite"></p>'
        f'<div id="empty-state" class="empty-state" hidden><p>No findings match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div>'
        f"{''.join(cards)}"
    )


def _format_remediation_executive_summary(
    findings: list[dict[str, Any]],
    latest_remediation: dict[str, dict[str, str]],
    has_remediation: bool,
) -> str:
    if not has_remediation:
        return "0 verified fixed (no remediation records supplied)."
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
    return ", ".join(rem_parts) + "."


def _render_audit_tables_and_limitations(
    audit: dict[str, Any],
) -> tuple[str, str, str]:
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
    return coverage, limitations_html, checks


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
        key=lambda f: (_SEVERITY_ORDER.index(f["severity"]), f["id"]),
    )
    findings_by_id = {f["id"]: f for f in findings}
    cards = [
        _render_audit_finding_card(
            f, evidence, findings_by_id, latest_remediation.get(f["id"])
        )
        for f in findings
    ]
    coverage, limitations_html, checks = _render_audit_tables_and_limitations(audit)
    comparison_html = _render_comparison_html(prior, audit)
    findings_body = _render_audit_findings_section(
        findings, cards, latest_remediation, has_remediation
    )
    record_label = (
        "1 finding record" if len(findings) == 1 else f"{len(findings)} finding records"
    )
    sev_summary = ", ".join(
        f"{sum(f['severity'] == s for f in findings)} {s}" for s in _SEVERITY_ORDER
    )
    rem_exec_text = _format_remediation_executive_summary(
        findings, latest_remediation, has_remediation
    )
    body = f"""<header><p class="muted">fred-skills · local evidence report</p><h1>Repository audit</h1><p>{text(audit["run_id"])} · {text(audit["repository"]["identity"])}</p><p>Revision: <code>{text(audit["repository"]["revision"])}</code> · dirty: {text(audit["repository"]["dirty"])}</p><nav aria-label="Report views"><a href="#executive">Executive</a><a href="#findings">Findings</a><a href="#coverage">Coverage</a><a href="#checks">Checks</a><a href="#comparison">Comparison</a></nav></header>
<section id="executive"><h2>Executive view</h2><p>{record_label}; {sum(f["status"] == "confirmed" for f in findings)} reproduced; {sum(f["status"] == "refuted" for f in findings)} refuted. These counts are not a repository safety score.</p><p><b>Severity counts:</b> {sev_summary}.</p><p><b>Remediation totals:</b> {rem_exec_text}</p>{limitations_html}</section>
<section id="findings"><h2>Review findings</h2>{findings_body}</section>
<section id="coverage"><h2>Coverage and continuation</h2><div class="table-container"><table><thead><tr><th>Topic</th><th>Scope</th><th>Status</th><th>Notes</th><th>Next action</th></tr></thead><tbody>{coverage}</tbody></table></div></section>
<section id="checks"><h2>Executed and unexecuted checks</h2><div class="table-container"><table><thead><tr><th>Check</th><th>State</th><th>Command</th><th>Result</th></tr></thead><tbody>{checks}</tbody></table></div></section><section id="comparison"><h2>Comparison</h2>{comparison_html}</section>"""
    return _page_shell(f"Repository audit — {text(audit['run_id'])}", body)


def _domain_artifact_label(domain_kind: str) -> str:
    if domain_kind == "observations":
        return "'observations.json' ('scanner-observations.json')"
    return f"'{domain_kind}.json'"


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
        artifact_label = _domain_artifact_label(domain_kind)
        if prior is not None:
            raise FredCliError(
                f"Prior comparison ('--prior') requires 'audit.json' ledgers; domain artifact {artifact_label} is not supported",
                code="unsupported_domain_artifact",
                hint="Omit --prior when rendering a domain report, or pass 'audit.json' ledgers.",
            )
        if remediation is not None:
            raise FredCliError(
                f"Remediation records ('--remediation') require 'audit.json' ledgers; domain artifact {artifact_label} is not supported",
                code="unsupported_domain_artifact",
                hint="Omit --remediation when rendering a domain report, or pass an 'audit.json' ledger.",
            )
        renderers = {
            "architecture": _render_architecture_html,
            "review": _render_review_html,
            "testing": _render_testing_html,
            "spec": _render_spec_html,
        }
        if domain_kind not in renderers:
            raise FredCliError(
                f"Report rendering ('report') requires an 'audit.json' ledger; artifact {artifact_label} is not supported",
                code="unsupported_domain_artifact",
                hint="Pass an 'audit.json' ledger or primary suite deliverable.",
            )
        _validate_domain_payload(audit, domain_kind)
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


_INLINE_CODE_SPAN_RE = re.compile(r"(`+)(?!`)([^\r\n]+?)(?<!`)\1(?!`)")
_GITHUB_MENTION_RE = re.compile(
    r"(^|(?<![A-Za-z0-9_]))@([A-Za-z0-9_][A-Za-z0-9_-]*(?:/[A-Za-z0-9_][A-Za-z0-9_-]*)?)"
)


def _escape_plain_markdown_segment(segment: str) -> str:
    escaped = segment.replace("<", "&lt;").replace(">", "&gt;")
    escaped = escaped.replace("`", r"\`")
    escaped = escaped.replace("~~~", r"\~~~")
    return _GITHUB_MENTION_RE.sub(r"\1`@\2`", escaped)


def _escape_inline_markdown_line(line: str) -> str:
    parts: list[str] = []
    last = 0
    for match in _INLINE_CODE_SPAN_RE.finditer(line):
        parts.append(_escape_plain_markdown_segment(line[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_escape_plain_markdown_segment(line[last:]))
    return "".join(parts)


def _sanitize_markdown_line_structure(line: str) -> str:
    sanitized = re.sub(r"^(\s*)(#+)", r"\1\\\2", line)
    return re.sub(
        r"^(\s*)([=-]{2,}|\*{3,}|_{3,})(\s*)$",
        lambda m: f"{m.group(1)}\\{m.group(2)}{m.group(3)}",
        sanitized,
    )


def _sanitize_issue_inline(value: object) -> str:
    collapsed = " ".join(str(value).replace("\x00", "").splitlines())
    escaped = _escape_inline_markdown_line(collapsed)
    return _sanitize_markdown_line_structure(escaped)


def _sanitize_issue_block(value: object) -> str:
    normalized = (
        str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [
        _sanitize_markdown_line_structure(_escape_inline_markdown_line(ln))
        for ln in normalized.split("\n")
    ]
    return "\n".join(lines)


def _fence_issue_commands(commands: list[str]) -> str:
    if not commands:
        return ""
    cleaned = [
        str(cmd).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        for cmd in commands
    ]
    joined = "\n".join(cleaned)
    max_ticks = max(
        (len(match.group(0)) for match in re.finditer(r"`+", joined)),
        default=0,
    )
    fence = "`" * max(3, max_ticks + 1)
    return f"{fence}\n{joined}\n{fence}"


def _validate_issue_drafts_inputs(audit: Any, output: Path, fmt: str) -> None:
    if fmt not in ("markdown", "json"):
        raise FredCliError(
            f"Unsupported issue format: {fmt}",
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
        artifact_label = _domain_artifact_label(domain_kind)
        raise FredCliError(
            f"Issue drafts ('issues') require an 'audit.json' ledger; domain artifact {artifact_label} is not supported",
            code="unsupported_domain_artifact",
            hint="Pass an 'audit.json' ledger from a repository-audits run.",
        )
    if audit_errors(audit):
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


def _render_issue_finding_sections(
    fid: str,
    f: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
) -> list[str]:
    safe_fid = _sanitize_issue_inline(fid)
    safe_ftitle = _sanitize_issue_inline(f["title"])
    safe_status = _sanitize_issue_inline(f["status"])
    safe_severity = _sanitize_issue_inline(f["severity"])
    safe_confidence = _sanitize_issue_inline(f["confidence"])
    evidence_lines = "\n".join(
        f"- {_sanitize_issue_inline(evidence[eid]['path'])}:{evidence[eid]['line_start']}–{evidence[eid]['line_end']} ({_sanitize_issue_inline(evidence[eid]['symbol'])}): {_sanitize_issue_inline(evidence[eid]['context'])}"
        for eid in f["evidence_ids"]
    )
    contract_ids_str = ", ".join(
        _sanitize_issue_inline(cid) for cid in f["contract_ids"]
    )
    sequence_lines = "\n".join(
        f"{i}. {_sanitize_issue_inline(step)}"
        for i, step in enumerate(f["sequence"], 1)
    )
    acceptance_lines = "\n".join(
        "- " + _sanitize_issue_inline(a) for a in f["acceptance"]
    )
    val_summary = (
        _sanitize_issue_inline(f["validation"]["state"])
        + ": "
        + _sanitize_issue_inline(f["validation"]["result"])
    )
    commands_block = _fence_issue_commands(f["validation"]["commands"])
    finding_sections = [
        f"## {safe_fid}: {safe_ftitle}",
        f"Evidence: {safe_status}; severity: {safe_severity}; confidence: {safe_confidence}",
        _sanitize_issue_block(f["impact"]),
        "### Preconditions",
        _sanitize_issue_block(f["preconditions"]),
        "### Expected and actual behavior",
        "Expected: " + _sanitize_issue_inline(f["expected"]),
        "Actual: " + _sanitize_issue_inline(f["actual"]),
        "### Source evidence",
        evidence_lines,
        "### Contract and cause",
        "Contract IDs: " + contract_ids_str,
        _sanitize_issue_block(f["root_cause"]),
        "### Failure sequence",
        sequence_lines,
        "### Counterevidence",
        _sanitize_issue_block(f["counterevidence"]),
        "### Proposed correction",
        _sanitize_issue_block(f["remedy"]),
        "### Acceptance",
        acceptance_lines,
        "### Validation",
        val_summary,
    ]
    if commands_block:
        finding_sections.append(commands_block)
    return finding_sections


def _write_issue_draft_file(
    issue: dict[str, Any],
    safe_issue_title: str,
    body_text: str,
    output: Path,
    fmt: str,
) -> str:
    if fmt == "json":
        payload = {
            "title": safe_issue_title,
            "body": body_text,
            "labels": list(issue.get("labels", [])),
            "assignees": list(issue.get("assignees", [])),
        }
        path = output / (issue["id"] + ".json")
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        path = output / (issue["id"] + ".md")
        path.write_text(body_text + "\n", encoding="utf-8")
    return str(path)


def issue_drafts(audit: dict, output: Path, format: str = "markdown") -> list[str]:
    _validate_issue_drafts_inputs(audit, output, format)
    findings = {f["id"]: f for f in audit["findings"]}
    evidence = {e["id"]: e for e in audit["evidence"]}
    paths: list[str] = []
    for issue in audit["issues"]:
        if issue["publication"] != "draft":
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", issue["id"]):
            raise ValueError("Issue IDs must be filename-safe")
        safe_issue_title = _sanitize_issue_inline(issue["title"])
        sections = [
            f"# {safe_issue_title}",
            "Publication state: local draft; no external write performed.",
        ]
        for fid in issue["finding_ids"]:
            sections.extend(
                _render_issue_finding_sections(fid, findings[fid], evidence)
            )
        deps_str = ", ".join(_sanitize_issue_inline(d) for d in issue["depends_on"])
        sections.append("Dependencies: " + deps_str)
        body_text = "\n\n".join(sections)
        paths.append(
            _write_issue_draft_file(issue, safe_issue_title, body_text, output, format)
        )
    return paths
