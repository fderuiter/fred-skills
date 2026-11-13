Use repo-audit-behavioral-contracts to conduct a comprehensive behavioral contract reconstruction audit of <repository> at <revision>.

Prioritize <critical workflows> and inspect the whole relevant scope in bounded slices.
Reuse <existing audit workspace, if any>. Establish intended contracts independently,
trace callers and enforcement, construct adversarial sequences, and check counterevidence.
Separate reproduced defects, statically supported findings, unresolved risks, hardening,
and refuted candidates. Report file/symbol/line evidence tied to the audited snapshot.
Record honest coverage, tests actually run, validation not run, severity and confidence,
minimal remedies, independent acceptance criteria, and cross-topic handoffs. Keep IDs
stable and preserve unrelated records. Return a standalone report plus audit.json.
Draft any issue recommendations; do not modify application code or publish issues unless
the current user request separately authorizes it.
