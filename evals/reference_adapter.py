"""Deterministic fixture adapter for harness self-tests. This is NOT an AI evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ACTOR: dict[str, Any] = {"kind": "deterministic-fixture-adapter", "model": None}


def _handle_audit(request: str, source: Path) -> dict[str, Any]:
    keys: list[tuple[str, str]] = []
    if (
        "Proofread this sentence only" not in request
        and "Sibling routing request:" not in request
    ):
        if (
            source / "session.mjs"
        ).exists() and "if (mine === this.generation)" not in (
            source / "session.mjs"
        ).read_text(encoding="utf-8"):
            keys.append(
                (
                    "stale-publication",
                    "session.mjs: completion assignment is unconditional",
                )
            )
        if (source / "records.py").exists() and 'record["owner_id"] != user_id' not in (
            source / "records.py"
        ).read_text(encoding="utf-8"):
            keys.append(
                (
                    "authorization-bypass",
                    "records.py: returned object is not bound to its authenticated owner",
                )
            )
        if (source / "transfer.py").exists() and "with db:" not in (
            source / "transfer.py"
        ).read_text(encoding="utf-8"):
            keys.append(
                (
                    "partial-commit",
                    "transfer.py: debit is committed before a possible interruption",
                )
            )
        if (source / "test_fee.py").exists() and "expected = fee(100)" in (
            source / "test_fee.py"
        ).read_text(encoding="utf-8"):
            keys.append(
                (
                    "circular-oracle",
                    "test_fee.py: expected result calls the same fee implementation",
                )
            )
        if (source / "candidates.json").exists():
            candidates = json.loads(
                (source / "candidates.json").read_text(encoding="utf-8")
            )
            keys.extend(
                (
                    key,
                    "candidates.json: supplied records share the named corrective mechanism",
                )
                for key in sorted({str(f["root_cause"]) for f in candidates})
            )
    return {
        "actor": ACTOR,
        "findings": [
            {
                "key": key,
                "status": "grouped"
                if key in {"publication-guard", "authorization"}
                else "supported",
                "evidence": evidence,
            }
            for key, evidence in keys
        ],
    }


def _handle_architecture(request: str, source: Path) -> dict[str, Any]:
    if "Sibling routing request:" in request:
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "title": "Architecture specification abstention",
            "status": "draft",
            "boundaries": [],
            "decisions": [],
        }
    if (source / "dispatch_adr_context.json").exists():
        ctx = json.loads(
            (source / "dispatch_adr_context.json").read_text(encoding="utf-8")
        )
        if ctx.get("decision_needed"):
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "title": "Order event dispatch architecture decision",
                "status": "accepted",
                "boundaries": [],
                "decisions": [
                    {
                        "id": "ADR-001",
                        "title": str(ctx["topic"]),
                        "context": "Checkout requires bounded latency with durable delivery.",
                        "decision": "Adopt transactional outbox queue over synchronous cross-service RPC.",
                        "consequences": [
                            "Checkout commits atomically with outbox event persistence.",
                            "Downstream consumers must handle idempotent at-least-once delivery.",
                        ],
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "title": "Settled architecture baseline",
            "status": "draft",
            "boundaries": [],
            "decisions": [],
        }

    if (source / "http_order_repo.py").exists():
        code = (source / "http_order_repo.py").read_text(encoding="utf-8")
        if "ValidatedOrderCommand" not in code:
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "title": "Order repository boundary specification",
                "status": "accepted",
                "boundaries": [
                    {
                        "name": "http-to-order-repository",
                        "type": "trust",
                        "description": "http_order_repo.py: isolate persistence from raw HTTP headers and bodies.",
                        "invariants": [
                            "Repository accepts only validated command value objects.",
                            "Tenant identity is authenticated before crossing into storage.",
                        ],
                    }
                ],
                "decisions": [],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "title": "Order repository boundary specification",
            "status": "accepted",
            "boundaries": [],
            "decisions": [],
        }

    if (source / "pipeline_modules.py").exists():
        code = (source / "pipeline_modules.py").read_text(encoding="utf-8")
        if "run_billing_monolith" in code:
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "title": "Billing pipeline decomposition",
                "status": "accepted",
                "boundaries": [
                    {
                        "name": "billing-ingestion-module",
                        "type": "module",
                        "description": "pipeline_modules.py: separate CSV extraction, tax rules, and SMTP I/O.",
                        "invariants": [
                            "Tax calculation performs zero network side effects."
                        ],
                    }
                ],
                "decisions": [
                    {
                        "id": "ADR-DECOMP-1",
                        "title": "Split billing monolith into extractor, calculator, and notifier",
                        "context": "pipeline_modules.py mixes CSV parsing, tax arithmetic, and SMTP calls.",
                        "decision": "Extract pure domain calculation from ingestion and notification adapters.",
                        "consequences": [
                            "Enables deterministic unit testing without SMTP mocks."
                        ],
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "title": "Billing pipeline decomposition",
            "status": "accepted",
            "boundaries": [],
            "decisions": [],
        }

    return {
        "actor": ACTOR,
        "schema_version": "1.0",
        "title": "Architecture specification",
        "status": "draft",
        "boundaries": [],
        "decisions": [],
    }


def _handle_review(request: str, source: Path) -> dict[str, Any]:
    if "Sibling routing request:" in request:
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "verdict": "comment",
            "summary": "Abstained on sibling code-review specialist request.",
            "findings": [],
        }

    if (source / "change.diff").exists() and (source / "invoice_client.py").exists():
        diff_text = (source / "change.diff").read_text(encoding="utf-8")
        client_text = (source / "invoice_client.py").read_text(encoding="utf-8")
        if "+def format_invoice(total: int, currency: str) -> str:" in diff_text and (
            "format_invoice(total)" in client_text
        ):
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "Removed default currency parameter breaks existing invoice_client call site.",
                "findings": [
                    {
                        "id": "DIFF-001",
                        "title": "Breaking format_invoice signature change leaves caller unupdated",
                        "severity": "high",
                        "file": "invoice_client.py",
                        "line": 2,
                        "description": "formatter.py removes currency default while invoice_client.py calls format_invoice(total).",
                        "recommendation": "Preserve default currency='USD' or pass explicit currency at all call sites.",
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "Exported signature and caller invocations remain backward compatible.",
            "findings": [],
        }

    if (source / "escrow_state.py").exists():
        code = (source / "escrow_state.py").read_text(encoding="utf-8")
        if "dispute_resolved" not in code.split("def release_funds", 1)[-1]:
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "release_funds transitions DISPUTED escrow to RELEASED without invariant guard.",
                "findings": [
                    {
                        "id": "INV-001",
                        "title": "Unguarded DISPUTED to RELEASED state transition",
                        "severity": "critical",
                        "file": "escrow_state.py",
                        "line": 3,
                        "description": "escrow_state.py overwrites state to RELEASED without checking dispute_resolved.",
                        "recommendation": "Reject release_funds when state == 'DISPUTED' and not dispute_resolved.",
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "Disputed escrow transition guard preserves release invariant.",
            "findings": [],
        }

    if (source / "raw_comments.json").exists():
        comments = json.loads(
            (source / "raw_comments.json").read_text(encoding="utf-8")
        )
        if comments:
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "Consolidated duplicate expiry review observations into actionable remediation.",
                "findings": [
                    {
                        "id": "FB-001",
                        "title": "Enforce token expiration timestamp check in is_token_active",
                        "severity": "high",
                        "file": "auth_token.py",
                        "line": 4,
                        "description": "auth_token.py ignores now_epoch and accepts expired tokens with valid sub claims.",
                        "recommendation": "Compare int(claims.get('exp', 0)) > now_epoch before returning True.",
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "No unresolved review comments; token expiration check is enforced.",
            "findings": [],
        }

    return {
        "actor": ACTOR,
        "schema_version": "1.0",
        "verdict": "comment",
        "summary": "No review findings.",
        "findings": [],
    }


def _handle_testing(request: str, source: Path) -> dict[str, Any]:
    if "Sibling routing request:" in request:
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "component": "repository",
            "framework": "unittest",
            "test_cases": [],
        }

    if (source / "discount_calc.py").exists():
        if (source / "discount_checks_a.py").exists():
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "component": "discount_calc",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "GAP-NEG-SUBTOTAL",
                        "name": "test_negative_subtotal_raises_value_error",
                        "description": "discount_calc.py: assert compute_discount(-1, 10) raises ValueError.",
                        "oracle_type": "exception",
                        "verified": False,
                    },
                    {
                        "id": "GAP-MAX-CAP",
                        "name": "test_percentage_above_hundred_is_clamped",
                        "description": "discount_calc.py: assert compute_discount(200, 150) clamps discount to 200.",
                        "oracle_type": "boundary",
                        "verified": False,
                    },
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "component": "discount_calc",
            "framework": "unittest",
            "test_cases": [],
        }

    if (source / "rate_limiter.py").exists():
        if not (source / "tests/existing_rate_limiter_checks.py").exists():
            gen_test = source / "tests/test_rate_limiter_generated.py"
            gen_test.parent.mkdir(parents=True, exist_ok=True)
            gen_test.write_text(
                "import unittest\n"
                "from rate_limiter import allow_request\n\n\n"
                "class RateLimiterBoundaryTests(unittest.TestCase):\n"
                "    def test_burst_limit_boundary(self) -> None:\n"
                "        self.assertTrue(allow_request(2, 3))\n"
                "        self.assertFalse(allow_request(3, 3))\n"
                "        self.assertFalse(allow_request(0, 0))\n",
                encoding="utf-8",
                newline="\n",
            )
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "component": "rate_limiter",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "TC-BURST-LIMIT",
                        "name": "test_burst_limit_boundary",
                        "description": "tests/test_rate_limiter_generated.py: assert exact burst limit and zero limit rejection.",
                        "oracle_type": "boundary",
                        "verified": True,
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "component": "rate_limiter",
            "framework": "unittest",
            "test_cases": [],
        }

    if (source / "cache_store.py").exists():
        if (source / "suite_probe_a.py").exists():
            return {
                "actor": ACTOR,
                "schema_version": "1.0",
                "component": "cache_store",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "VERIFY-STATE-ISOLATION",
                        "name": "test_record_event_hermetic_across_reordering",
                        "description": "cache_store.py: module-global _SHARED_STATE leaks event counts across test cases.",
                        "oracle_type": "state",
                        "verified": False,
                    }
                ],
            }
        return {
            "actor": ACTOR,
            "schema_version": "1.0",
            "component": "cache_store",
            "framework": "unittest",
            "test_cases": [],
        }

    return {
        "actor": ACTOR,
        "schema_version": "1.0",
        "component": "repository",
        "framework": "unittest",
        "test_cases": [],
    }


def _handle_workflow(request: str, source: Path) -> dict[str, Any]:
    is_routing = "Sibling routing request:" in request

    if "finding-verification.schema.json" in request:
        if is_routing:
            return {
                "actor": ACTOR,
                "finding_id": "none",
                "reproduction_status": "needs-validation",
                "safe_control_outcomes": [],
                "confidence_rating": "low",
                "notes": "Abstained on sibling workflow request.",
            }
        code = (source / "ledger_writer.py").read_text(encoding="utf-8")
        if "with self._lock:" not in code:
            return {
                "actor": ACTOR,
                "finding_id": "F-101",
                "reproduction_status": "confirmed",
                "safe_control_outcomes": [
                    {"control": "single-writer-control", "passed": True}
                ],
                "confidence_rating": "high",
                "commands": ["python -c 'import ledger_writer'"],
                "result": "Confirmed unsynchronized append on LedgerWriter.entries.",
            }
        return {
            "actor": ACTOR,
            "finding_id": "F-101",
            "reproduction_status": "refuted",
            "safe_control_outcomes": [
                {"control": "caller-lock-enforced", "passed": True}
            ],
            "confidence_rating": "high",
            "commands": ["python -c 'import ledger_writer'"],
            "result": "Refuted: LedgerWriter.append acquires self._lock.",
        }

    if "regression.schema.json" in request:
        if is_routing or (source / "tests/existing_quota_regression.py").exists():
            return {
                "actor": ACTOR,
                "finding_id": "none",
                "failure_boundaries": ["none"],
                "test_command_traces": ["noop: no new regression required"],
                "assertion_expectations": ["existing coverage or out-of-scope request"],
                "notes": "No regression test written.",
            }
        reg_file = source / "tests/test_quota_regression_spec.py"
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        reg_file.write_text(
            "import unittest\n"
            "from quota_ledger import debit_quota\n\n\n"
            "class QuotaRegressionTests(unittest.TestCase):\n"
            "    def test_overdraft_rejected(self) -> None:\n"
            "        with self.assertRaises(ValueError):\n"
            "            debit_quota(10, 25)\n",
            encoding="utf-8",
            newline="\n",
        )
        return {
            "actor": ACTOR,
            "finding_id": "F-201",
            "test_file": "tests/test_quota_regression_spec.py",
            "failure_boundaries": [
                "quota_ledger.debit_quota(available=10, requested=25)"
            ],
            "test_command_traces": [
                {
                    "command": "python -m unittest tests.test_quota_regression_spec",
                    "exit_code": 1,
                    "output": "AssertionError: ValueError not raised",
                }
            ],
            "assertion_expectations": [
                {
                    "description": "Overdraft exceeding available quota must raise ValueError",
                    "expected": "ValueError('insufficient quota')",
                    "actual": "-15",
                }
            ],
        }

    if "plan.schema.json" in request:
        if is_routing:
            return {
                "actor": ACTOR,
                "task_breakdowns": [
                    {
                        "task_id": "noop",
                        "title": "Defer out-of-scope sibling request",
                        "file_scopes": [],
                        "acceptance_criteria": ["Route to verification specialist"],
                    }
                ],
                "dependency_order": [],
                "file_scopes": [],
                "acceptance_criteria": [],
            }
        items = json.loads(
            (source / "verified_findings.json").read_text(encoding="utf-8")
        )
        confirmed = [item for item in items if item.get("status") == "confirmed"]
        if not confirmed:
            return {
                "actor": ACTOR,
                "task_breakdowns": [
                    {
                        "task_id": "noop",
                        "title": "No confirmed findings require remediation",
                        "file_scopes": [],
                        "acceptance_criteria": ["Preserve refuted baseline"],
                    }
                ],
                "dependency_order": [],
                "file_scopes": [],
                "acceptance_criteria": [],
            }
        return {
            "actor": ACTOR,
            "task_breakdowns": [
                {
                    "task_id": str(item["task_id"]),
                    "title": f"Remediate {item['finding_id']} in {item['file']}",
                    "file_scopes": [str(item["file"])],
                    "blocked_by": list(item.get("blocked_by", [])),
                    "acceptance_criteria": [
                        f"Verify {item['finding_id']} regression and safe controls pass"
                    ],
                }
                for item in confirmed
            ],
            "dependency_order": [str(item["task_id"]) for item in confirmed],
            "file_scopes": [str(item["file"]) for item in confirmed],
            "acceptance_criteria": [
                f"Verify {item['finding_id']} regression passes" for item in confirmed
            ],
        }

    if "fix.schema.json" in request:
        target = source / "retry_budget.py"
        code = target.read_text(encoding="utf-8") if target.exists() else ""
        if is_routing or "if attempts >= max_attempts:" in code:
            return {
                "actor": ACTOR,
                "file_changes": ["none"],
                "diff_summaries": ["No source changes applied."],
                "execution_results": [
                    {"command": "noop-check", "exit_code": 0, "passed": True}
                ],
                "compatibility_limits": ["Unchanged source state preserved."],
            }
        target.write_text(
            "# Contract: stop retrying once attempts reach max_attempts.\n"
            "def can_retry(attempts: int, max_attempts: int) -> bool:\n"
            "    if attempts >= max_attempts:\n"
            "        return False\n"
            "    return attempts >= 0\n",
            encoding="utf-8",
            newline="\n",
        )
        return {
            "actor": ACTOR,
            "finding_id": "F-RETRY-1",
            "file_changes": [
                {
                    "path": "retry_budget.py",
                    "action": "modified",
                    "additions": 2,
                    "deletions": 1,
                }
            ],
            "diff_summaries": [
                {
                    "path": "retry_budget.py",
                    "summary": "Enforce attempts >= max_attempts upper bound in can_retry.",
                }
            ],
            "execution_results": [
                {
                    "command": "python -c 'from retry_budget import can_retry; assert not can_retry(3, 3) and can_retry(1, 3)'",
                    "exit_code": 0,
                    "passed": True,
                }
            ],
            "compatibility_limits": [
                "Callers passing negative max_attempts receive False."
            ],
        }

    if "verification.schema.json" in request:
        if is_routing:
            return {
                "actor": ACTOR,
                "before_revision": "unchanged",
                "after_revision": "unchanged",
                "original_reproduced": False,
                "regression_passed": False,
                "safe_controls_passed": True,
                "commands": ["noop: abstained on sibling request"],
                "result": "abstained",
            }
        vdata = json.loads(
            (source / "verification_inputs.json").read_text(encoding="utf-8")
        )
        orig_repro = not bool(vdata["zero_divisor_handled_before"])
        reg_passed = bool(vdata["zero_divisor_handled_after"])
        safe_passed = bool(vdata["nonzero_controls_preserved_after"])
        return {
            "actor": ACTOR,
            "before_revision": str(vdata["before_revision"]),
            "after_revision": str(vdata["after_revision"]),
            "original_reproduced": orig_repro,
            "regression_passed": reg_passed,
            "safe_controls_passed": safe_passed,
            "commands": ["python -m unittest verify_division_suite"],
            "result": "verified-fixed"
            if (orig_repro and reg_passed and safe_passed)
            else "safe-control-regression",
        }

    if "reconciliation.schema.json" in request:
        if is_routing:
            return {
                "actor": ACTOR,
                "finding_fingerprint_matches": [],
                "remediation_state_transitions": [],
                "unmapped_records": [],
            }
        rdata = json.loads(
            (source / "reconcile_inputs.json").read_text(encoding="utf-8")
        )
        matches = []
        transitions = []
        for item in rdata["findings"]:
            fid = str(item["finding_id"])
            fp = str(item["fingerprint"])
            prev = str(item["previous_state"])
            proved = bool(item["has_verification_proof"])
            matches.append(
                {"fingerprint": fp, "finding_id": fid, "match_status": "exact"}
            )
            if proved:
                transitions.append(
                    {
                        "finding_id": fid,
                        "fingerprint": fp,
                        "previous_state": prev,
                        "new_state": "verified-fixed",
                        "reason": "Matched fingerprint with structured verification proof.",
                        "verification_proof": f"proof:{fid}",
                    }
                )
            else:
                transitions.append(
                    {
                        "finding_id": fid,
                        "fingerprint": fp,
                        "previous_state": prev,
                        "new_state": "open",
                        "reason": "Kept open because verification proof is absent.",
                    }
                )
        return {
            "actor": ACTOR,
            "finding_fingerprint_matches": matches,
            "remediation_state_transitions": transitions,
            "unmapped_records": list(rdata["unmapped"]),
        }

    raise ValueError("Unsupported workflow contract in prompt")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = args.prompt.read_text(encoding="utf-8")
    source = args.prompt.parent / "source"

    if "conforming to architecture.schema.json" in request:
        payload = _handle_architecture(request, source)
    elif "conforming to review.schema.json" in request:
        payload = _handle_review(request, source)
    elif "conforming to testing.schema.json" in request:
        payload = _handle_testing(request, source)
    elif any(
        schema_name in request
        for schema_name in (
            "finding-verification.schema.json",
            "regression.schema.json",
            "plan.schema.json",
            "fix.schema.json",
            "verification.schema.json",
            "reconciliation.schema.json",
        )
    ):
        payload = _handle_workflow(request, source)
    else:
        payload = _handle_audit(request, source)

    args.output.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
