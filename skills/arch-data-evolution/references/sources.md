# Research references

Reviewed 2026-11-18. Use these as persistence-evolution patterns, not evidence about an uninspected repository.
Confirm the target database engine version and locking semantics before applying DDL advice.

- [Pramod Sadalage & Martin Fowler — Evolutionary Database Design](https://martinfowler.com/articles/evodb.html): Expand-migrate-contract transition phases for continuous schema evolution.
- [PostgreSQL Documentation — Explicit Locking & Concurrent Indexes](https://www.postgresql.org/docs/current/explicit-locking.html): Table lock modes and non-blocking DDL constraints during live traffic.
