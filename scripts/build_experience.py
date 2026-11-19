"""Generate a searchable catalog, task/pack/host documentation, and Pages site bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import shutil
from html import escape
from pathlib import Path
from typing import Any

from fredlib.common import ROOT, read_json
from fredlib.reports import SCRIPT, STYLE
from skill_tools import build_outputs

HOSTED_PAGES_URL = "https://fderuiter.github.io/fred-skills"
SITE_ROOT_FILES: tuple[str, ...] = (
    "README.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "SECURITY.md",
    "LICENSE",
    "VERSION",
    "gemini-extension.json",
    "evals/README.md",
    ".claude-plugin/marketplace.json",
    ".cursor-plugin/marketplace.json",
)
SITE_DIRECTORIES: tuple[str, ...] = (
    "docs",
    "catalog",
    "schemas",
    "skills",
)
EXCLUDED_SITE_NAMES: frozenset[str] = frozenset({"__pycache__", ".DS_Store"})
_SUITE_LABELS: dict[str, str] = {
    "repository-audits": "Audits",
    "repository-workflows": "Verified engineering",
    "software-architecture": "Software architecture",
    "code-review": "Code review",
    "automated-testing": "Automated testing",
}


def _suite_label(suite: dict[str, Any]) -> str:
    name = str(suite.get("name", ""))
    return str(
        suite.get("title")
        or _SUITE_LABELS.get(name)
        or name.replace("-", " ").capitalize()
    )


def _render_explorer_html(
    catalog: list[dict[str, Any]],
    recipes: dict[str, dict[str, Any]],
    suites: list[dict[str, Any]] | None = None,
    *,
    root_prefix: str = "../",
    docs_prefix: str = "",
) -> str:
    if suites is None:
        suites = read_json(ROOT / "catalog/suites.json")["suites"]
    skill_suites = {
        str(entry["name"]): str(entry["suite"])
        for entry in catalog
        if entry.get("name") and entry.get("suite")
    }
    cards: list[str] = []
    for entry in catalog:

        def _esc(key: str, item: dict[str, Any] = entry) -> str:
            return escape(str(item.get(key, "")), quote=True)

        cards.append(
            f'<article class="finding" id="{_esc("name")}" data-status="{_esc("suite")}"><h2><a href="{root_prefix}{_esc("path")}">{_esc("title")}</a></h2><p><code>{_esc("name")}</code></p><p>{_esc("description")}</p><p>Symptoms: {escape(", ".join(entry.get("symptoms", [])))}</p><p>Maturity: {_esc("maturity")}</p><details><summary>Inputs and outcomes</summary><p>{escape("; ".join(entry.get("expected_inputs", [])))}</p><p>{escape("; ".join(entry.get("outcomes", [])))}</p><p>{_esc("behavior_evaluation")}</p></details></article>'
        )
    task_blocks: list[str] = []
    for name, item in recipes.items():
        member_skills = [str(skill) for skill in item["skills"]]
        member_suites = list(
            dict.fromkeys(skill_suites[s] for s in member_skills if s in skill_suites)
        )
        search_terms = (
            f"{name} {item['outcome']} {' '.join(item.get('workflows', []))}".lower()
        )
        task_blocks.append(
            f'<details class="task" id="task-{escape(name, quote=True)}" data-skills="{escape(" ".join(member_skills), quote=True)}" data-suites="{escape(" ".join(member_suites), quote=True)}" data-search="{escape(search_terms, quote=True)}"><summary>{escape(name)} — {escape(item["outcome"])}</summary><pre>python scripts/fred.py plan /path/to/repo --task {escape(name)}'
            + (" --mode changes --base main" if item.get("requires_base") else "")
            + "</pre><p>"
            + ", ".join(
                f'<a href="#{escape(skill, quote=True)}">{escape(skill)}</a>'
                for skill in member_skills
            )
            + '</p><p class="muted">Outputs: '
            + escape(", ".join(item.get("outputs", [])))
            + "</p></details>"
        )
    tasks = "".join(task_blocks)
    status_options = "".join(
        f'<option value="{escape(str(suite["name"]), quote=True)}">{escape(_suite_label(suite))}</option>'
        for suite in suites
    )
    h = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none';style-src 'unsafe-inline';script-src 'sha256-{h}';base-uri 'none';connect-src 'none'"><title>fred-skills task explorer</title><style>{STYLE}</style></head><body><header><h1>Find an engineering workflow</h1><p>Choose an outcome, then inspect a small relevant set of portable skills.</p><nav><a href="{root_prefix}README.md">Quickstart</a><a href="{docs_prefix}experience.md">Task guide</a><a href="{docs_prefix}catalog.md">Catalog</a><a href="{docs_prefix}toolkit.md">CLI guide</a><a href="{docs_prefix}validation.md">What has been tested</a></nav></header><section aria-label="Search and filter"><label for="query">Task or symptom <input id="query" type="search" placeholder="database migration, memory leak, review-pr…"></label><label for="status">Family <select id="status"><option value="">All</option>{status_options}</select></label><p id="count" data-noun="skills" role="status" aria-live="polite"></p><div id="empty-state" class="empty-state" hidden><p>No tasks or skills match the current filter.</p><button id="reset-filters" type="button">Reset filters</button></div></section><section id="tasks" data-filter-section><h2>Start from your task</h2>{tasks}</section><section id="skills" data-filter-section><h2>Search skills</h2>{"".join(cards)}</section><script>{SCRIPT}</script></body></html>"""


def outputs(root: Path = ROOT) -> dict[str, str]:
    catalog = read_json(root / "catalog/skills.json")["skills"]
    recipes = read_json(root / "catalog/recipes.json")["recipes"]
    packs = read_json(root / "catalog/packs.json")["packs"]
    hosts = read_json(root / "catalog/hosts.json")["hosts"]
    suites = read_json(root / "catalog/suites.json")["suites"]
    known = {item["name"] for item in catalog}
    for name, data in {**recipes, **packs}.items():
        if not data["skills"] or set(data["skills"]) - known:
            raise ValueError(f"Unknown or empty skill selection: {name}")
    doc = (
        "# Choose a task or pack\n\n"
        "Generated by `python scripts/build_experience.py`. "
        f"Browse the [hosted explorer]({HOSTED_PAGES_URL}) "
        "or [open the offline explorer](explorer.html).\n\n"
    )
    doc += "## Tasks\n\n| Task | Expected outcome | Selected skills | Expected outputs |\n| --- | --- | --- | --- |\n"
    for name, task in recipes.items():
        doc += (
            f"| `{name}` | {task['outcome']} | {', '.join(task['skills'])} | "
            f"{', '.join(task.get('outputs', []))} |\n"
        )
    doc += "\n## Packs\n\n| Pack | Purpose | Packages |\n| --- | --- | --- |\n"
    for name, pack in packs.items():
        doc += f"| `{name}` | {pack['description']} | {len(pack['skills'])} |\n"
    doc += "\n## Host adapters\n\nPaths are relative to the explicit project/home destination. Filesystem checks are not host behavior certification. See the [integration guide](integration.md) for host setup and orchestration instructions.\n\n| Host | Project | User | Behavioral status |\n| --- | --- | --- | --- |\n"
    for name, host in hosts.items():
        doc += f"| {name} | `{host['project_path']}` | `{host['user_path']}` | {host['behavior_test']} |\n"
    html = _render_explorer_html(
        catalog, recipes, suites, root_prefix="../", docs_prefix=""
    )
    return {"docs/experience.md": doc, "docs/explorer.html": html}


def build(root: Path = ROOT, check: bool = False) -> list[str]:
    return build_outputs(outputs(root), root=root, check=check)


def _ignore_site_entries(_directory: str, contents: list[str]) -> set[str]:
    return {
        name
        for name in contents
        if name in EXCLUDED_SITE_NAMES
        or name.endswith((".pyc", ".pyo", ".pyd", ".telemetry.json"))
    }


def stage_site(output_dir: Path, root: Path = ROOT) -> Path:
    """Stage a clean static GitHub Pages bundle with .nojekyll into output_dir."""
    resolved_root = root.resolve()
    resolved_out = (
        output_dir.resolve()
        if output_dir.is_absolute()
        else (resolved_root / output_dir).resolve()
    )
    if resolved_out == resolved_root or resolved_root.is_relative_to(resolved_out):
        raise ValueError(
            f"Refusing to stage site into repository root or ancestor: {resolved_out}"
        )

    catalog = read_json(resolved_root / "catalog/skills.json")["skills"]
    recipes = read_json(resolved_root / "catalog/recipes.json")["recipes"]
    suites = read_json(resolved_root / "catalog/suites.json")["suites"]
    generated = outputs(resolved_root)
    root_index_html = _render_explorer_html(
        catalog,
        recipes,
        suites,
        root_prefix="",
        docs_prefix="docs/",
    )

    if resolved_out.exists():
        shutil.rmtree(resolved_out)
    resolved_out.mkdir(parents=True, exist_ok=True)

    (resolved_out / ".nojekyll").write_text("", encoding="utf-8", newline="\n")
    (resolved_out / "index.html").write_text(
        root_index_html, encoding="utf-8", newline="\n"
    )

    for rel_dir in SITE_DIRECTORIES:
        src_dir = resolved_root / rel_dir
        if src_dir.is_dir():
            shutil.copytree(
                src_dir,
                resolved_out / rel_dir,
                ignore=_ignore_site_entries,
            )

    for rel_file in SITE_ROOT_FILES:
        src_file = resolved_root / rel_file
        if src_file.is_file():
            dst_file = resolved_out / rel_file
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)

    for rel_gen, content in generated.items():
        gen_target = resolved_out / rel_gen
        gen_target.parent.mkdir(parents=True, exist_ok=True)
        gen_target.write_text(content, encoding="utf-8", newline="\n")

    return resolved_out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--stage-site",
        type=Path,
        default=None,
        help="Optional output directory (e.g. dist/site) for staging the static Pages bundle.",
    )
    args = parser.parse_args()
    errors = build(check=args.check)
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(True)
    if args.stage_site is not None:
        staged = stage_site(args.stage_site, root=ROOT)
        print(f"PASS: staged static site in {staged.as_posix()}")
    print("PASS: task discovery documentation and offline explorer")
    raise SystemExit(0)
