# Research references

Reviewed 2026-11-18. Use these as resilience engineering references, not evidence about an uninspected repository.
Confirm runtime concurrency semantics and proxy configuration before applying budgets.

- [AWS Builders' Library — Timeouts, Retries, and Backoff with Jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/): Retry amplification math, exponential backoff with jitter, and token-bucket retry budgets.
- [Google SRE Book — Addressing Cascading Failures](https://sre.google/sre-book/addressing-cascading-failures/): Load shedding, deadline propagation, queue bounds, and graceful degradation.
