# Investigation method: Cohesion and Responsibility

## Scope selection

Review one critical workflow end to end, then expand by resource/actor/data ownership.
For each inventory class, record concrete instances, relevant source paths, existing
enforcement, and coverage. Do not mark an entire class reviewed from one example.

## Contract worksheet

Record the exact predicate or guarantee, independent requirement basis, scope/lifetime,
establishing operation, invalidating operations, observers, enforcing authority, and
existing tests. If intended semantics are unknown, record the question and keep the
finding at needs-validation or hardening until a supported requirement exists.

## Topic-specific discovery probes

Apply the five topic-specific investigation steps in SKILL.md to each inventoried boundary. Record both passing safeguards and unresolved cases.

## Adversarial case

A report formatter also updates authorization policy, so a formatting change unexpectedly changes write permissions.

Find matching reachable code, name the actors and boundaries, and write exact numbered
steps. Inspect the strongest prevention mechanism. If no real path matches, report
that this illustrative failure was not established; do not turn the example into evidence.

## Safe control

A large parser can be cohesive if its branches implement one format contract under one ownership model.

Preserve the safe control while selecting a correction. Compare mechanisms, not syntax.

## Independent validation

Check the proposed seam against representative change scenarios and existing behavior, then specify boundary tests for the extracted responsibility.

Derive expected results from the contract. Use a bounded deterministic local harness
where practical, record actual commands/results, and retain limitations. An unavailable
tool changes the validation plan, not the claimed certainty. Record the smallest missing
piece of evidence that would resolve each open finding.

## Cross-topic boundary

Keep this topic's inventory complete but hand off a different root cause to the specialist
named in SKILL.md. Preserve the same evidence IDs and avoid issuing duplicate defect
reports for one underlying contract violation. Distinct independently fixable causes
remain separate even when they produce the same observed symptom.
