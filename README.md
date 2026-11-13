# fred-skills

Reusable Agent Skills for evidence-based repository audits. The first suite contains
**35 skills**: 33 focused audit domains, a coordinator, and a findings-to-issues workflow.

Start with [Repository Audit Program](skills/repo-audit-program/SKILL.md), select an
individual audit from the [catalog](docs/catalog.md), or use
[Audit Findings and Issue Planning](skills/repo-audit-synthesis/SKILL.md) on existing results.

## Use a skill

Copy the selected directory from `skills/` into your agent host's documented skill
location, retaining its name and contents. Each package includes its own references
and templates. The portable entry point is `SKILL.md`; `agents/openai.yaml` provides
optional OpenAI-specific display metadata. Invocation syntax and discovery locations
depend on the host. See the [usage guide](docs/usage.md).

```text
Use repo-audit-program to audit <repository> at <revision> for hidden behavioral
contracts. Prioritize <critical workflows>. Preserve evidence, existing safeguards,
honest coverage, severity/confidence, and actual validation results across all three
passes. Return coherent issue drafts and an ordered implementation handoff.
Keep application code unchanged and leave issues as drafts for this run.
```

Skills default to audits and reviewable drafts. Publishing issues or changing the
audited application requires an applicable user instruction. No analyzer or agent
runtime is installed or invoked automatically by this repository.

## Audit coverage

| Pass | Focus |
| --- | --- |
| Behavioral discovery | Contracts, invariants, state machines, concurrency, ownership/lifecycle, ordering, domain models |
| Correctness and resilience | Failure, cancellation, trust, transactions, replay, data/protocol integrity, configuration, compatibility, resource bounds |
| Architecture and maintainability | Boundaries, side effects, data authority, tests/oracles, cohesion, observability, determinism, APIs, accessibility, obsolete paths |

Findings distinguish reproduced defects, statically supported defects, unresolved
risks, hardening, and refuted candidates. A shared evidence ledger lets later passes
reuse earlier contracts and consolidate causes without filing one issue per occurrence.

## Authoring and validation

The repository keeps installable skills self-contained while maintaining shared
resources from one canonical source. The [architecture](docs/architecture.md),
[contribution guide](CONTRIBUTING.md), and [machine-readable catalog](catalog/skills.json)
describe the layout and maintenance workflow.

```sh
python -m pip install -r requirements-dev.txt
python scripts/sync_audit_resources.py --check
python scripts/build_catalog.py --check
python scripts/validate_skills.py
python -m unittest discover -s tests -v
```

Python is needed for authoring checks and the optional audit-ledger validator, not
instruction-only use. The [validation record](docs/validation.md) separates deterministic
checks and sampled agent runs from the 105 specified evaluation scenarios. Full behavior
across all specialist topics and agent hosts has not been established.

See [research and existing approaches](docs/research.md) for primary-source references,
tool boundaries, and adoption decisions. The workflows are original authored material;
third-party skill implementations are not bundled.
