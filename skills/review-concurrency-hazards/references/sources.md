# Research references

Reviewed 2026-11-18. Use these as concurrency-review references, not evidence about an uninspected diff.
Confirm the target language memory model and worker topology before reporting races.

- [Brian Goetz et al. — Java Concurrency in Practice (Lock Ordering & Publication)](https://jcip.net/): State ownership, safe publication, check-then-act races, and lock-order deadlock prevention.
- [Python Documentation — Developing with asyncio (Concurrency and Multithreading)](https://docs.python.org/3/library/asyncio-dev.html): Event-loop yield boundaries, task cancellation, and thread-safety across `await` points.
