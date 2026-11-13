# <Actionable outcome-oriented title>

## Problem and current behavior

Describe the concrete trigger, violated contract, scope, and user/system consequence.
Include finding IDs, status, severity rationale, and confidence. Mark investigation-only
work explicitly when a precondition remains unverified.

## Evidence and failure sequence

Give verified snapshot, file/symbol/lines or immutable permalinks, actual safeguard analysis,
and a numbered reproducer/interleaving. Separate executed reproduction from static reasoning.
Do not expose credentials, private data, or sensitive exploit details in a public issue.

## Proposed scope

State the smallest coherent root-cause correction, boundaries, and compatibility/migration
considerations. Put unproven architectural alternatives under decisions, not requirements.

## Acceptance criteria

- <Independent observable postcondition under the failing scenario.>
- <The known safe scenario and compatibility guarantee remain valid.>
- <A deterministic regression crosses the boundary that was previously unverified.>

## Validation

Existing checks, proposed regression, commands verified available, actual results if run,
and limitations. A specification is not a test execution.

## Relationships and implementation handoff

Root-cause finding IDs, existing issue/PR links, true blockers, independent work,
implementation order, likely files, and remaining decisions. Do not invent issue numbers,
assignees, labels, estimates, or readiness. An epic groups independently shippable issues;
one issue should not be created for every occurrence of the same root cause.
