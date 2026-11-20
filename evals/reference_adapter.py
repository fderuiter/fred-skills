"""Deterministic fixture adapter for harness self-tests. This is NOT an AI evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ACTOR: dict[str, Any] = {"kind": "deterministic-fixture-adapter", "model": None}

# (filename, prompt_phrase, marker_in_a, topic, root_cause, symbol, title)
_SINGLE_FINDING_AUDIT_RULES: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    (
        "coordinator.py",
        "for multi-topic coordination",
        "SHARED_LEDGER_OVERWRITE = True",
        "program",
        "uncoordinated-shared-ledger-overwrite",
        "run_passes",
        "Specialist pass loop overwrites shared findings array without ID range isolation",
    ),
    (
        "triage_bundle.py",
        "diagnose this stalled repository audit session",
        "REDACT_BEARER_TOKENS = False",
        "diagnostics",
        "unredacted-bundle-secret",
        "export_bundle",
        "Recovery bundle exports raw bearer credentials from stalled session transcript",
    ),
    (
        "job_runner.mjs",
        "for behavioral contract reconstruction",
        "// UNGUARDED_REPLACEMENT_EMIT",
        "behavioral-contracts",
        "superseded-operation-event",
        "JobRunner.replace",
        "Superseded job emits completion event after replacement epoch advances",
    ),
    (
        "seat_allocator.py",
        "for implicit invariants",
        "UNLOCKED_CAPACITY_CHECK = True",
        "invariants",
        "capacity-overcommit",
        "reserve_seat",
        "Unlocked inclusive capacity check permits seat reservations past limit",
    ),
    (
        "upload_status.py",
        "for implicit state machines",
        "LEAVES_COMPLETED_FLAG = True",
        "state-machines",
        "contradictory-status-flags",
        "retry_upload",
        "Retry transition sets pending flag without clearing prior completion flag",
    ),
    (
        "camera_preview.mjs",
        "for ownership and lifecycle boundaries",
        "// MISSING_UNMOUNT_GUARD",
        "lifecycle",
        "post-unmount-resource-leak",
        "CameraPreview.mount",
        "Late camera stream remains open when component unmounts before mount resolves",
    ),
    (
        "worker_client.mjs",
        "for temporal coupling",
        "// PREMATURE_READY_SET",
        "temporal-coupling",
        "premature-ready-publication",
        "WorkerClient.initialize",
        "WorkerClient sets ready flag before awaited transport connection resolves",
    ),
    (
        "enrollment.py",
        "for implicit domain models",
        "SWAPPED_PRIMITIVE_IDS = True",
        "domain-models",
        "primitive-identifier-transposition",
        "assign_participant",
        "Primitive string study_id and site_id parameters are transposed across call",
    ),
    (
        "payout_sender.py",
        "for failure-mode completeness",
        "BLIND_TIMEOUT_RETRY = True",
        "failure-modes",
        "unacknowledged-commit-duplicate-retry",
        "submit_payout",
        "Lost acknowledgement after ledger append triggers duplicate payout retry",
    ),
    (
        "skill_upload_helper.py",
        "for agent configuration, skill bundle, and mcp server security risks",
        "PLAINTEXT_MCP_EXFIL = True",
        "agent-configs",
        "credential-exfiltration-and-plaintext-mcp",
        "upload_diagnostics",
        "Agent helper reads SSH private key material and posts it externally",
    ),
    (
        "webhook_charger.py",
        "for idempotency and replay safety",
        "NON_ATOMIC_DEDUP = True",
        "idempotency",
        "non-atomic-dedup-check",
        "process_charge_event",
        "Non-atomic event deduplication check allows duplicate concurrent charges",
    ),
    (
        "order_store.py",
        "for error semantics and taxonomy",
        "SWALLOW_WRITE_ERROR = True",
        "error-semantics",
        "write-error-collapsed-to-empty-result",
        "persist_and_list_orders",
        "Storage write failure is caught and collapsed into an empty order list",
    ),
    (
        "frame_ingestor.py",
        "for resource bounds and backpressure",
        "UNBOUNDED_FRAME_BUFFER = True",
        "capacity",
        "unbounded-frame-queue",
        "enqueue_frame",
        "Frame queue ignores max_frames limit and grows without backpressure",
    ),
    (
        "search_fetcher.mjs",
        "for cancellation propagation",
        "// LOSING_BRANCH_MUTATES_STATE",
        "cancellation",
        "unpropagated-abort-side-effect",
        "SearchFetcher.run",
        "Aborted Promise.race branch continues and overwrites latestResults",
    ),
    (
        "profile_cache.mjs",
        "for cache coherence",
        "// STALE_READ_OVERWRITES_INVALIDATION",
        "cache-coherence",
        "stale-read-repopulation",
        "ProfileCache.getProfile",
        "In-flight read started before invalidation repopulates cache with stale value",
    ),
    (
        "dosage_serializer.py",
        "for data transformation integrity",
        "LOSSY_BINARY_FLOAT = True",
        "data-integrity",
        "binary-float-precision-loss",
        "serialize_ledger_amount",
        "Decimal amount is converted through binary float before wire serialization",
    ),
    (
        "packet_framer.py",
        "for boundary-condition correctness",
        "INCLUSIVE_END_INDEX = True",
        "boundary-conditions",
        "inclusive-upper-bound-overflow",
        "write_chunk",
        "Inclusive range upper bound writes one byte past allocated frame buffer",
    ),
    (
        "frame_codec.py",
        "for protocol contract drift",
        "CHAR_COUNT_HEADER = True",
        "protocol-drift",
        "char-vs-byte-length-drift",
        "encode_frame",
        "Encoder writes code-point count while decoder slices by UTF-8 byte length",
    ),
    (
        "codec_flags.py",
        "for configuration combinatorics",
        "MISSING_CROSS_FLAG_VALIDATION = True",
        "configuration",
        "incompatible-codec-flag-pair",
        "build_pipeline_config",
        "Pipeline accepts zstd input advertisement while zstd decoder is disabled",
    ),
    (
        "checkpoint_migrator.py",
        "for compatibility and migration contracts",
        "DROPS_UNKNOWN_SCHEMA_FIELDS = True",
        "compatibility",
        "downgrade-overwrite-field-loss",
        "save_client_record",
        "Older v1 client writer discards unknown forward-schema fields on save",
    ),
    (
        "chunk_assembler.py",
        "for algorithmic complexity",
        "RESCANS_ALL_PRIOR_CHUNKS = True",
        "complexity",
        "quadratic-chunk-reassembly",
        "append_chunk",
        "Per-chunk join and checksum rescan causes quadratic transfer cost",
    ),
    (
        "webhook_allowlist.py",
        "for security-sensitive canonicalization",
        "PREFIX_ALLOWLIST_CHECK = True",
        "canonicalization",
        "userinfo-host-allowlist-bypass",
        "is_allowed_target",
        "String prefix allowlist accepts trusted host in URL userinfo component",
    ),
    (
        "export_dashboard.py",
        "for hidden side effects",
        "PREVIEW_MUTATES_RECORDS = True",
        "side-effects",
        "query-mutates-export-state",
        "get_pending_preview",
        "Preview query mutates exported flag in place and drops subsequent exports",
    ),
    (
        "transfer_tracker.py",
        "for duplicated sources of truth",
        "USES_ROUNDED_PERCENT_AUTHORITY = True",
        "sources-of-truth",
        "divergent-completion-authority",
        "is_transfer_complete",
        "Cached percentage counter reports completion before all blocks decode",
    ),
    (
        "reporting_writer.py",
        "for architectural boundaries and dependency direction",
        "BYPASSES_LEDGER_SERVICE = True",
        "architecture",
        "direct-cross-module-table-write",
        "record_adjustment",
        "Reporting writer mutates ledger_tables directly bypassing domain service",
    ),
    (
        "transfer_checks.py",
        "for untested behavioral contracts",
        "OMITS_RESUME_ASSERTION = True",
        "test-contracts",
        "untested-interruption-resume-contract",
        "verify_transfer_suite",
        "Test suite omits end-to-end interruption and resume byte-identity check",
    ),
    (
        "report_renderer.py",
        "for cohesion and responsibility",
        "COUPLES_FORMATTER_TO_ACL = True",
        "cohesion",
        "formatter-mutates-auth-policy",
        "render_summary_csv",
        "CSV formatter mutates external write authorization policy flag",
    ),
    (
        "payment_retrier.py",
        "for observability gaps",
        "DROPS_CORRELATION_FIELDS = True",
        "observability",
        "missing-retry-correlation-context",
        "execute_with_retry",
        "Retry log omits request_id and attempt count preventing duplicate correlation",
    ),
    (
        "ttl_expiry_check.py",
        "for nondeterministic behavior",
        "USES_UNCONTROLLED_WALL_CLOCK = True",
        "nondeterminism",
        "wall-clock-tick-assumption",
        "verify_token_ttl",
        "TTL check compares exact wall-clock second tick susceptible to jitter",
    ),
    (
        "resource_deleter.py",
        "for api design consistency",
        "MIXED_SYNC_ASYNC_DELETE = True",
        "api-consistency",
        "inconsistent-delete-completion-semantics",
        "purge_tenant_assets",
        "Sibling delete_* helpers mix durable deletion with asynchronous queueing",
    ),
    (
        "modal_controller.mjs",
        "for accessibility state consistency",
        "// LEAVES_ARIA_EXPANDED_ON_HIDE",
        "accessibility-state",
        "hidden-modal-focus-and-expanded-desync",
        "ModalController.dismissOnError",
        "Hiding modal on error leaves trigger aria-expanded true and focus un-restored",
    ),
    (
        "legacy_poller.py",
        "for dead code and obsolete compatibility paths",
        "RETIRED_FLAG_STILL_REGISTERS = True",
        "dead-code",
        "retired-flag-import-listener-leak",
        "register_import_hooks",
        "Retired compatibility flag still registers global listeners at import time",
    ),
)


def _with_usage(payload: dict[str, Any], request: str) -> dict[str, Any]:
    token_estimate = max(64, len(request.split()) * 4)
    return {
        **payload,
        "actor": ACTOR,
        "usage": {"tokens": token_estimate},
    }


def _build_audit_ledger(
    scope_file: str,
    topic: str,
    detected: list[tuple[str, str, str, str]],
) -> dict[str, Any]:
    # detected items: (topic_name, root_cause, symbol, title)
    evidence: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if not detected:
        evidence.append(
            {
                "id": "EV-1",
                "path": scope_file,
                "line_start": 1,
                "line_end": 2,
                "symbol": "module",
                "context": f"Inspected {scope_file} within requested scope.",
            }
        )

    for idx, (f_topic, root_cause, symbol, title) in enumerate(detected, start=1):
        ev_id = f"EV-{idx}"
        ct_id = f"C-{idx}"
        f_id = f"F-{idx}"
        evidence.append(
            {
                "id": ev_id,
                "path": scope_file,
                "line_start": 1,
                "line_end": 4,
                "symbol": symbol,
                "context": f"{scope_file}:{symbol} exhibits {root_cause}.",
            }
        )
        contracts.append(
            {
                "id": ct_id,
                "dimension": f_topic,
                "scope": scope_file,
                "intended": f"{symbol} preserves {f_topic} behavioral contract.",
                "observed": title,
                "enforcement": "missing-guard",
                "evidence_ids": [ev_id],
            }
        )
        findings.append(
            {
                "id": f_id,
                "topic": f_topic,
                "tags": [f_topic, root_cause],
                "title": title,
                "status": "supported",
                "severity": "high",
                "confidence": "high",
                "contract_ids": [ct_id],
                "evidence_ids": [ev_id],
                "preconditions": f"Reachable invocation of {symbol} in {scope_file}.",
                "sequence": [
                    f"Caller invokes {symbol} in {scope_file}.",
                    f"Missing safeguard triggers {root_cause}.",
                ],
                "expected": f"{symbol} enforces its documented {f_topic} contract.",
                "actual": title,
                "impact": f"Violates {f_topic} safety invariant in {scope_file}.",
                "root_cause": root_cause,
                "counterevidence": f"Checked {scope_file}; no compensating caller guard exists.",
                "remedy": f"Enforce the {root_cause} invariant directly in {scope_file}:{symbol}.",
                "acceptance": [
                    f"Regression test verifies {symbol} upholds {f_topic} contract."
                ],
                "validation": {
                    "state": "not-run",
                    "commands": [],
                    "result": "Static source inspection completed in deterministic fixture harness.",
                },
                "related_findings": [],
            }
        )
        if f_topic == "synthesis":
            issues.append(
                {
                    "id": f"ISS-{idx}",
                    "title": f"Remediate {root_cause}",
                    "finding_ids": [f_id],
                    "depends_on": [],
                    "related_issues": [],
                    "existing_url": None,
                    "publication": "draft",
                }
            )

    return {
        "schema_version": "1.0",
        "run_id": f"eval-{topic}",
        "repository": {
            "identity": "eval-fixture",
            "revision": "0000000",
            "dirty": False,
            "snapshot": scope_file,
        },
        "scope": [scope_file],
        "coverage": [
            {
                "topic": topic,
                "scope": scope_file,
                "status": "inspected",
                "evidence_ids": [evidence[0]["id"]],
                "notes": f"Evaluated {scope_file} against {topic} criteria.",
                "next_action": "none",
            }
        ],
        "contracts": contracts,
        "evidence": evidence,
        "findings": findings,
        "issues": issues,
        "limitations": ["Synthetic fixture evaluation scope."],
        "checks": [],
    }


def _handle_audit(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()

    if (source / "session.mjs").exists():
        detected: list[tuple[str, str, str, str]] = []
        code = (source / "session.mjs").read_text(encoding="utf-8")
        if (
            "for concurrency and race conditions" in req_lower
            and "if (mine === this.generation)" not in code
        ):
            detected.append(
                (
                    "concurrency",
                    "stale-publication",
                    "Session.start",
                    "Unconditional completion assignment publishes stale generation value",
                )
            )
        return _build_audit_ledger("session.mjs", "concurrency", detected)

    if (source / "records.py").exists():
        detected = []
        code = (source / "records.py").read_text(encoding="utf-8")
        if (
            "for trust and authorization boundaries" in req_lower
            and 'record["owner_id"] != user_id' not in code
        ):
            detected.append(
                (
                    "trust-boundaries",
                    "authorization-bypass",
                    "read_record",
                    "Record lookup returns value without binding owner_id to authenticated user",
                )
            )
        return _build_audit_ledger("records.py", "trust-boundaries", detected)

    if (source / "transfer.py").exists():
        detected = []
        code = (source / "transfer.py").read_text(encoding="utf-8")
        if (
            "for implicit transaction boundaries" in req_lower
            and "with db:" not in code
        ):
            detected.append(
                (
                    "transactions",
                    "partial-commit",
                    "transfer",
                    "Account debit commits before potential failure leaving credit unapplied",
                )
            )
        return _build_audit_ledger("transfer.py", "transactions", detected)

    if (source / "test_fee.py").exists():
        detected = []
        code = (source / "test_fee.py").read_text(encoding="utf-8")
        if "for test oracle quality" in req_lower and "expected = fee(100)" in code:
            detected.append(
                (
                    "test-oracles",
                    "circular-oracle",
                    "test_fee",
                    "Test recomputes expected fee by calling the same production fee function",
                )
            )
        return _build_audit_ledger("test_fee.py", "test-oracles", detected)

    if (source / "candidates.json").exists():
        detected = []
        if "for findings synthesis and issue planning" in req_lower:
            candidates = json.loads(
                (source / "candidates.json").read_text(encoding="utf-8")
            )
            for cause in sorted({str(item["root_cause"]) for item in candidates}):
                detected.append(
                    (
                        "synthesis",
                        cause,
                        cause,
                        f"Consolidated candidate findings sharing {cause} corrective mechanism",
                    )
                )
        return _build_audit_ledger("candidates.json", "synthesis", detected)

    for (
        filename,
        prompt_phrase,
        marker,
        topic,
        root_cause,
        symbol,
        title,
    ) in _SINGLE_FINDING_AUDIT_RULES:
        target = source / filename
        if target.exists():
            detected = []
            code = target.read_text(encoding="utf-8")
            if prompt_phrase in req_lower and marker in code:
                detected.append((topic, root_cause, symbol, title))
            return _build_audit_ledger(filename, topic, detected)

    any_file = next(
        (
            p.relative_to(source).as_posix()
            for p in sorted(source.rglob("*"))
            if p.is_file()
        ),
        "README.txt",
    )
    return _build_audit_ledger(any_file, "general", [])


def _handle_architecture(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()
    abstain_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "title": "Architecture specification abstention",
        "status": "draft",
        "boundaries": [],
        "decisions": [],
    }
    if (source / "dispatch_adr_context.json").exists():
        if "architectural decision record and system trade-offs" not in req_lower:
            return abstain_payload
        ctx = json.loads(
            (source / "dispatch_adr_context.json").read_text(encoding="utf-8")
        )
        if ctx.get("decision_needed"):
            return {
                "schema_version": "1.0",
                "title": "Order event dispatch architecture decision",
                "status": "accepted",
                "boundaries": [],
                "decisions": [
                    {
                        "id": "ADR-1",
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
            "schema_version": "1.0",
            "title": "Settled architecture baseline",
            "status": "draft",
            "boundaries": [],
            "decisions": [],
        }

    if (source / "http_order_repo.py").exists():
        if "system, service, module, and trust boundaries" not in req_lower:
            return abstain_payload
        code = (source / "http_order_repo.py").read_text(encoding="utf-8")
        if "ValidatedOrderCommand" not in code:
            return {
                "schema_version": "1.0",
                "title": "Order repository boundary specification",
                "status": "accepted",
                "boundaries": [
                    {
                        "name": "boundary-1",
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
            "schema_version": "1.0",
            "title": "Order repository boundary specification",
            "status": "accepted",
            "boundaries": [],
            "decisions": [],
        }

    if (source / "pipeline_modules.py").exists():
        if "decompose system capabilities into cohesive components" not in req_lower:
            return abstain_payload
        code = (source / "pipeline_modules.py").read_text(encoding="utf-8")
        if "run_billing_monolith" in code:
            return {
                "schema_version": "1.0",
                "title": "Billing pipeline decomposition",
                "status": "accepted",
                "boundaries": [
                    {
                        "name": "module-1",
                        "type": "module",
                        "description": "pipeline_modules.py: separate CSV extraction, tax rules, and SMTP I/O.",
                        "invariants": [
                            "Tax calculation performs zero network side effects."
                        ],
                    }
                ],
                "decisions": [
                    {
                        "id": "ADR-1",
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
            "schema_version": "1.0",
            "title": "Billing pipeline decomposition",
            "status": "accepted",
            "boundaries": [],
            "decisions": [],
        }

    return abstain_payload


def _handle_review(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()
    abstain_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "verdict": "comment",
        "summary": "Abstained on sibling code-review specialist request.",
        "findings": [],
    }

    if (source / "change.diff").exists() and (source / "invoice_client.py").exists():
        if "analyze pull request diffs" not in req_lower:
            return abstain_payload
        diff_text = (source / "change.diff").read_text(encoding="utf-8")
        client_text = (source / "invoice_client.py").read_text(encoding="utf-8")
        if "+def format_invoice(total: int, currency: str) -> str:" in diff_text and (
            "format_invoice(total)" in client_text
        ):
            return {
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "Removed default currency parameter breaks existing invoice_client call site.",
                "findings": [
                    {
                        "id": "FINDING-1",
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
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "Exported signature and caller invocations remain backward compatible.",
            "findings": [],
        }

    if (source / "escrow_state.py").exists():
        if "review code changes against behavioral invariants" not in req_lower:
            return abstain_payload
        code = (source / "escrow_state.py").read_text(encoding="utf-8")
        if "dispute_resolved" not in code.split("def release_funds", 1)[-1]:
            return {
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "release_funds transitions DISPUTED escrow to RELEASED without invariant guard.",
                "findings": [
                    {
                        "id": "FINDING-1",
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
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "Disputed escrow transition guard preserves release invariant.",
            "findings": [],
        }

    if (source / "raw_comments.json").exists():
        if (
            "synthesize review findings into prioritized, actionable feedback"
            not in req_lower
        ):
            return abstain_payload
        comments = json.loads(
            (source / "raw_comments.json").read_text(encoding="utf-8")
        )
        if comments:
            return {
                "schema_version": "1.0",
                "verdict": "request_changes",
                "summary": "Consolidated duplicate expiry review observations into actionable remediation.",
                "findings": [
                    {
                        "id": "FINDING-1",
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
            "schema_version": "1.0",
            "verdict": "approve",
            "summary": "No unresolved review comments; token expiration check is enforced.",
            "findings": [],
        }

    return abstain_payload


def _handle_testing(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()
    abstain_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "component": "repository",
        "framework": "unittest",
        "test_cases": [],
    }

    if (source / "discount_calc.py").exists():
        if "discover test coverage gaps" not in req_lower:
            return abstain_payload
        if (source / "discount_checks_a.py").exists():
            return {
                "schema_version": "1.0",
                "component": "discount_calc",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "TC-1",
                        "name": "test_negative_subtotal_raises_value_error",
                        "description": "discount_calc.py: assert compute_discount(-1, 10) raises ValueError.",
                        "oracle_type": "exception",
                        "verified": False,
                    },
                    {
                        "id": "TC-2",
                        "name": "test_percentage_above_hundred_is_clamped",
                        "description": "discount_calc.py: assert compute_discount(200, 150) clamps discount to 200.",
                        "oracle_type": "boundary",
                        "verified": False,
                    },
                ],
            }
        return {
            "schema_version": "1.0",
            "component": "discount_calc",
            "framework": "unittest",
            "test_cases": [],
        }

    if (source / "rate_limiter.py").exists():
        if "generate automated regression and unit test cases" not in req_lower:
            return abstain_payload
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
                "schema_version": "1.0",
                "component": "rate_limiter",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "TC-1",
                        "name": "test_burst_limit_boundary",
                        "description": "tests/test_rate_limiter_generated.py: assert exact burst limit and zero limit rejection.",
                        "oracle_type": "boundary",
                        "verified": True,
                    }
                ],
            }
        return {
            "schema_version": "1.0",
            "component": "rate_limiter",
            "framework": "unittest",
            "test_cases": [],
        }

    if (source / "cache_store.py").exists():
        if "execute and verify generated test suites" not in req_lower:
            return abstain_payload
        if (source / "suite_probe_a.py").exists():
            return {
                "schema_version": "1.0",
                "component": "cache_store",
                "framework": "unittest",
                "test_cases": [
                    {
                        "id": "TC-1",
                        "name": "test_record_event_hermetic_across_reordering",
                        "description": "cache_store.py: module-global _SHARED_STATE leaks event counts across test cases.",
                        "oracle_type": "state",
                        "verified": False,
                    }
                ],
            }
        return {
            "schema_version": "1.0",
            "component": "cache_store",
            "framework": "unittest",
            "test_cases": [],
        }

    return abstain_payload


def _handle_workflow(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()

    if (source / "ledger_writer.py").exists():
        if (
            "verify, reproduce, or refute the reported repository finding"
            not in req_lower
        ):
            return {
                "finding_id": "none",
                "reproduction_status": "needs-validation",
                "safe_control_outcomes": [],
                "confidence_rating": "low",
                "notes": "Abstained on sibling workflow request.",
            }
        code = (source / "ledger_writer.py").read_text(encoding="utf-8")
        if "with self._lock:" not in code:
            return {
                "finding_id": "F-1",
                "reproduction_status": "confirmed",
                "safe_control_outcomes": [
                    {"control": "single-writer-control", "passed": True}
                ],
                "confidence_rating": "high",
                "commands": ["python -c 'import ledger_writer'"],
                "result": "Confirmed unsynchronized append on LedgerWriter.entries.",
            }
        return {
            "finding_id": "F-1",
            "reproduction_status": "refuted",
            "safe_control_outcomes": [
                {"control": "caller-lock-enforced", "passed": True}
            ],
            "confidence_rating": "high",
            "commands": ["python -c 'import ledger_writer'"],
            "result": "Refuted: LedgerWriter.append acquires self._lock.",
        }

    if (source / "quota_ledger.py").exists():
        if (
            "specify an independent regression test" not in req_lower
            or (source / "tests/existing_quota_regression.py").exists()
        ):
            return {
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
            "finding_id": "F-1",
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

    if (source / "verified_findings.json").exists():
        if "plan bounded implementation tasks" not in req_lower:
            return {
                "task_breakdowns": [
                    {
                        "task_id": "noop",
                        "title": "Defer out-of-scope sibling request",
                        "file_scopes": [],
                        "acceptance_criteria": ["Route to implementation specialist"],
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
        id_remap = {
            str(item["task_id"]): f"TASK-{idx}"
            for idx, item in enumerate(confirmed, start=1)
        }
        return {
            "task_breakdowns": [
                {
                    "task_id": id_remap[str(item["task_id"])],
                    "title": f"Remediate {item['finding_id']} in {item['file']}",
                    "file_scopes": [str(item["file"])],
                    "blocked_by": [
                        id_remap[str(dep)]
                        for dep in item.get("blocked_by", [])
                        if str(dep) in id_remap
                    ],
                    "acceptance_criteria": [
                        f"Verify {item['finding_id']} regression and safe controls pass"
                    ],
                }
                for item in confirmed
            ],
            "dependency_order": [id_remap[str(item["task_id"])] for item in confirmed],
            "file_scopes": [str(item["file"]) for item in confirmed],
            "acceptance_criteria": [
                f"Verify {item['finding_id']} regression passes" for item in confirmed
            ],
        }

    if (source / "retry_budget.py").exists():
        target = source / "retry_budget.py"
        code = target.read_text(encoding="utf-8")
        if (
            "apply an authorized correction" not in req_lower
            or "if attempts >= max_attempts:" in code
        ):
            return {
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
            "finding_id": "F-1",
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

    if (source / "verification_inputs.json").exists():
        if "verify whether the proposed correction resolves" not in req_lower:
            return {
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

    if (source / "reconcile_inputs.json").exists():
        if "reconcile audit findings, verified fixes" not in req_lower:
            return {
                "finding_fingerprint_matches": [],
                "remediation_state_transitions": [],
                "unmapped_records": [],
            }
        rdata = json.loads(
            (source / "reconcile_inputs.json").read_text(encoding="utf-8")
        )
        matches = []
        transitions = []
        for idx, item in enumerate(rdata["findings"], start=1):
            fid = f"F-{idx}"
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
                        "verification_proof": f"proof:{fp}",
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
            "finding_fingerprint_matches": matches,
            "remediation_state_transitions": transitions,
            "unmapped_records": list(rdata["unmapped"]),
        }

    if (source / "session_state.json").exists():
        if "compact this debugging session into a handoff" not in req_lower:
            return {
                "schema_version": "1.0",
                "objective": "Abstained on sibling workflow request",
                "repository": {"path": ".", "revision": "unchanged", "dirty": False},
                "verified_facts": [],
                "assumptions": [],
                "completed_steps": [],
                "dead_ends": [],
                "touched_files": [],
                "untouched_scope": ["."],
                "commands_run": [
                    {"command": "noop: abstained", "outcome": "informational"}
                ],
                "next_action": "abstained",
            }
        sdata = json.loads((source / "session_state.json").read_text(encoding="utf-8"))
        return {
            "schema_version": "1.0",
            "objective": str(sdata["objective"]),
            "repository": {
                "path": ".",
                "revision": str(sdata["revision"]),
                "dirty": bool(sdata["dirty"]),
            },
            "verified_facts": list(sdata["verified_facts"]),
            "assumptions": list(sdata["assumptions"]),
            "completed_steps": list(sdata["completed_steps"]),
            "dead_ends": list(sdata["dead_ends"]),
            "touched_files": list(sdata["touched_files"]),
            "untouched_scope": list(sdata["untouched_scope"]),
            "commands_run": list(sdata["commands_run"]),
            "next_action": str(sdata["next_action"]),
        }

    if (source / "commit_context.json").exists():
        if (
            "reply tersely with the fix snippet and draft a conventional commit"
            not in req_lower
        ):
            return {
                "schema_version": "1.0",
                "compression_level": "lite",
                "terse_response": "Abstained on sibling handoff request.",
                "preserved_exact_spans": ["session-handoff"],
                "commit_type": "chore",
                "commit_subject": "chore: abstain on sibling workflow request",
                "commit_body_bullets": [],
                "git_mutated": False,
            }
        cdata = json.loads((source / "commit_context.json").read_text(encoding="utf-8"))
        if not cdata.get("active_change"):
            return {
                "schema_version": "1.0",
                "compression_level": str(cdata["compression_level"]),
                "terse_response": str(cdata["summary"]),
                "preserved_exact_spans": list(cdata["exact_spans"]),
                "commit_type": "chore",
                "commit_subject": "chore: abstain when working tree has no diff",
                "commit_body_bullets": [],
                "git_mutated": False,
            }
        return {
            "schema_version": "1.0",
            "compression_level": str(cdata["compression_level"]),
            "terse_response": str(cdata["summary"]),
            "preserved_exact_spans": list(cdata["exact_spans"]),
            "commit_type": str(cdata["commit_type"]),
            "commit_scope": str(cdata["commit_scope"]),
            "commit_subject": str(cdata["commit_subject"]),
            "commit_body_bullets": list(cdata["body_bullets"]),
            "git_mutated": False,
        }

    raise ValueError("Unsupported workflow fixture")


def _handle_spec(request: str, source: Path) -> dict[str, Any]:
    req_lower = request.lower()
    abstain_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "title": "Specification routing abstention",
        "status": "draft",
        "interview_decisions": [],
        "new_file_scopes": [],
        "out_of_scope": [],
        "verification_commands": [],
    }
    if (source / "webhook_context.json").exists():
        if "conduct a pre-code specification interview" not in req_lower:
            return abstain_payload
        ctx = json.loads((source / "webhook_context.json").read_text(encoding="utf-8"))
        mod = str(ctx["existing_module"])
        if ctx.get("ambiguous"):
            return {
                "schema_version": "1.0",
                "title": "Webhook delivery retry specification",
                "status": "interviewing",
                "interview_decisions": [
                    {
                        "id": "DEC-1",
                        "category": "edge-case",
                        "question": "Should exhausted webhook retries move payloads to a dead-letter queue or drop after bounded backoff?",
                        "decision": "Pending operator selection between durable DLQ and bounded drop.",
                        "rationale": f"{mod}: existing dispatcher lacks retry persistence.",
                    }
                ],
                "new_file_scopes": [mod],
                "out_of_scope": ["CLI flags"],
                "verification_commands": ["pytest tests/test_dispatcher.py"],
            }
        return {
            "schema_version": "1.0",
            "title": "Webhook delivery retry specification",
            "status": "approved",
            "interview_decisions": [],
            "new_file_scopes": [mod],
            "out_of_scope": ["CLI flags"],
            "verification_commands": ["pytest tests/test_dispatcher.py"],
        }

    if (source / "spec_notes.json").exists():
        if "author the feature specification" not in req_lower:
            return abstain_payload
        notes = json.loads((source / "spec_notes.json").read_text(encoding="utf-8"))
        if notes.get("ready"):
            scope = str(notes["scope"])
            out_scope = str(notes["out_of_scope"])
            (source / "SPEC.md").write_text(
                f"# Feature Specification: {notes['title']}\n\n"
                f"- Approved scope: `{scope}`\n"
                f"- Out of scope: `{out_scope}`\n",
                encoding="utf-8",
                newline="\n",
            )
            return {
                "schema_version": "1.0",
                "title": str(notes["title"]),
                "status": "approved",
                "interview_decisions": [
                    {
                        "id": "DEC-1",
                        "category": "architecture",
                        "question": "Should CSV export stream row chunks or buffer full payloads in memory?",
                        "decision": "Stream bounded row chunks from exporter.py.",
                        "rationale": "exporter.py: keeps memory O(chunk) on large exports.",
                    }
                ],
                "requirements": [
                    {
                        "id": "REQ-1",
                        "description": "Stream export batches with bounded row memory.",
                        "priority": "must",
                    }
                ],
                "new_file_scopes": [scope],
                "out_of_scope": [out_scope],
                "verification_commands": ["pytest tests/test_exporter.py"],
            }
        return {
            "schema_version": "1.0",
            "title": str(notes["title"]),
            "status": "draft",
            "interview_decisions": [],
            "new_file_scopes": [],
            "out_of_scope": [],
            "verification_commands": [],
        }

    if (source / "convergence_inputs.json").exists():
        if "converge with the specification boundaries" not in req_lower:
            return abstain_payload
        cdata = json.loads(
            (source / "convergence_inputs.json").read_text(encoding="utf-8")
        )
        allowed = set(cdata.get("spec_scopes", []))
        excluded = set(cdata.get("out_of_scope", []))
        touched = list(cdata.get("touched_files", []))
        drifted = [p for p in touched if p in excluded or p not in allowed]
        if drifted or not cdata.get("tests_verified"):
            bad_target = drifted[0] if drifted else "verification_commands"
            return {
                "schema_version": "1.0",
                "title": "Webhook retry convergence check",
                "status": "diverged",
                "interview_decisions": [],
                "new_file_scopes": sorted(allowed),
                "out_of_scope": sorted(excluded),
                "verification_commands": ["pytest tests/test_dispatcher.py"],
                "convergence_checks": [
                    {
                        "id": "CHK-1",
                        "target": bad_target,
                        "status": "out-of-scope",
                        "evidence": f"convergence_inputs.json: {bad_target} violates specification scope boundaries.",
                    }
                ],
            }
        return {
            "schema_version": "1.0",
            "title": "Webhook retry convergence check",
            "status": "converged",
            "interview_decisions": [],
            "new_file_scopes": sorted(allowed),
            "out_of_scope": sorted(excluded),
            "verification_commands": ["pytest tests/test_dispatcher.py"],
            "convergence_checks": [
                {
                    "id": "CHK-1",
                    "target": touched[0],
                    "status": "aligned",
                    "evidence": f"convergence_inputs.json: {touched[0]} stays within specification scope and passes verification.",
                }
            ],
        }
    raise ValueError("Unsupported spec fixture")


def _dispatch(request: str, source: Path) -> dict[str, Any]:
    if any(
        (source / marker).exists()
        for marker in (
            "dispatch_adr_context.json",
            "http_order_repo.py",
            "pipeline_modules.py",
        )
    ):
        return _handle_architecture(request, source)
    if any(
        (source / marker).exists()
        for marker in ("invoice_client.py", "escrow_state.py", "raw_comments.json")
    ):
        return _handle_review(request, source)
    if any(
        (source / marker).exists()
        for marker in ("discount_calc.py", "rate_limiter.py", "cache_store.py")
    ):
        return _handle_testing(request, source)
    if any(
        (source / marker).exists()
        for marker in (
            "webhook_context.json",
            "spec_notes.json",
            "convergence_inputs.json",
        )
    ):
        return _handle_spec(request, source)
    if any(
        (source / marker).exists()
        for marker in (
            "ledger_writer.py",
            "quota_ledger.py",
            "verified_findings.json",
            "retry_budget.py",
            "verification_inputs.json",
            "reconcile_inputs.json",
            "session_state.json",
            "commit_context.json",
        )
    ):
        return _handle_workflow(request, source)
    return _handle_audit(request, source)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = args.prompt.read_text(encoding="utf-8")
    source = args.prompt.parent / "source"
    payload = _with_usage(_dispatch(request, source), request)
    args.output.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
