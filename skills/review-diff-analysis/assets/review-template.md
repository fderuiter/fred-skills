# Code Review Feedback Template

## Pull Request Summary

- **Target Branch**: [e.g. main]
- **Changed Files**: [Count / List]
- **Review Verdict**: [Approve | Request Changes | Comment]

## Findings

### [Finding Title]
- **Severity**: [Critical | High | Medium | Low | Advisory]
- **Location**: `path/to/file:line`
- **Description**: [Detailed explanation of defect or invariant violation]
- **Recommendation**: [Concrete remediation guidance or code suggestion]

## Invariant Adherence Summary

- **Preserved Invariants & Safe Controls**: [Verified behaviors and refuted false-positive leads]
- **Compatibility, Security & Concurrency Checks**: [Summary of contract, boundary, and lock/async checks]
- **Potential Regressions & Deferred Leads**: [Areas requiring follow-up testing or specialist confirmation]
