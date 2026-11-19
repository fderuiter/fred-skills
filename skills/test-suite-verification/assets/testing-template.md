# Automated Testing Specification Template

## Test Suite Scope

- **Target Component**: [Component / Module Name]
- **Tested Behaviors**: [Contracts / Invariants to Verify]
- **Target Framework**: [e.g. pytest, unittest, jest]
- **Write Authorization**: [Workspace specification only | Repository edits explicitly permitted]

## Test Cases

### [Test Case Name]
- **Priority**: [Critical | High | Medium | Low]
- **Oracle Type**: [State | Exception | Output | Boundary]
- **Objective**: [Behavior tested and defect avoided]
- **Preconditions**: [Setup and isolated environment state]
- **Inputs & Action**: [Invocation steps]
- **Expected Outcome**: [Independent assertion and verification oracle]

## Execution Summary

- **Execution State**: [not_run | passed | failed | partial]
- **Counts**: [Total / Passed / Failed / Skipped]
- **Commands Executed**: [e.g. `pytest tests/test_target.py`]
- **Coverage & Flakiness Notes**: [Gaps addressed and determinism checks]
