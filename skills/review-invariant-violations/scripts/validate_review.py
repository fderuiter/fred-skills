#!/usr/bin/env python3
"""Validate code-review artifact structure, file paths, and verdict consistency."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping
from typing import Any


def _is_repo_relative(raw_path: str) -> bool:
    if not raw_path or "\\" in raw_path:
        return False
    posix = pathlib.PurePosixPath(raw_path)
    win = pathlib.PureWindowsPath(raw_path)
    return not (posix.is_absolute() or win.is_absolute() or ".." in posix.parts)


def validate(data: Any, schema: Mapping[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError as e:
        raise RuntimeError(
            "Dependency missing: jsonschema. Use an approved environment with jsonschema; no validation performed."
        ) from e
    errors = [
        (("/".join(map(str, e.absolute_path)) or "root") + ": " + e.message)
        for e in jsonschema.Draft202012Validator(schema).iter_errors(data)
    ]
    if errors or not isinstance(data, Mapping):
        return errors

    for top_path in data.get("paths", []):
        if isinstance(top_path, str) and not _is_repo_relative(top_path):
            errors.append(
                f"Deliverable path must be repository-relative (paths): {top_path}"
            )

    for cov in data.get("coverage", []):
        if isinstance(cov, Mapping):
            for cov_path in cov.get("paths", []):
                if isinstance(cov_path, str) and not _is_repo_relative(cov_path):
                    errors.append(
                        f"Deliverable path must be repository-relative (coverage.paths): {cov_path}"
                    )

    verdict = data.get("verdict")
    seen_ids: set[str] = set()
    dup_ids: set[str] = set()
    blocking_findings: list[str] = []

    for finding in data.get("findings", []):
        if not isinstance(finding, Mapping):
            continue
        fid = finding.get("id")
        if isinstance(fid, str):
            if fid in seen_ids and fid not in dup_ids:
                errors.append(f"Duplicate IDs in findings: {fid}")
                dup_ids.add(fid)
            seen_ids.add(fid)

        fpath = finding.get("file")
        if isinstance(fpath, str) and not _is_repo_relative(fpath):
            errors.append(
                f"Finding file path must be repository-relative: {fid or fpath}"
            )
        for extra_path in finding.get("paths", []):
            if isinstance(extra_path, str) and not _is_repo_relative(extra_path):
                errors.append(
                    f"Finding file path must be repository-relative: {fid or extra_path}"
                )

        if finding.get("severity") in ("critical", "high") and isinstance(fid, str):
            blocking_findings.append(fid)

    if verdict == "approve" and blocking_findings:
        errors.append(
            f"Verdict 'approve' conflicts with critical/high findings: {', '.join(blocking_findings)}"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=pathlib.Path)
    parser.add_argument(
        "--schema",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1]
        / "assets/review.schema.json",
    )
    a = parser.parse_args()
    try:
        errors = validate(
            json.loads(a.artifact.read_text(encoding="utf-8")),
            json.loads(a.schema.read_text(encoding="utf-8")),
        )
    except (OSError, ValueError) as e:
        print(f"Invalid input: {e}", file=sys.stderr)
        return 2
    except (ImportError, RuntimeError) as e:
        print(f"{e}", file=sys.stderr)
        print(
            "Remediation: pip install jsonschema (or pip install -r requirements-dev.txt)",
            file=sys.stderr,
        )
        return 2
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(
        "PASS: review structure, relative file paths, and verdict consistency; semantic claims require review."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
