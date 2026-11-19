---
name: terse-output-commit
description: "Use when enforcing terse low-token responses that keep code exact or drafting 50-character Conventional Commit messages."
---

# Terse Output and Commit

Read [workflow protocol](references/workflow-protocol.md) once before applying response compression or drafting commits.
Compress conversational prose only; never sacrifice code accuracy, symbol fidelity, or safety boundaries.

## Workflow

1. Select compression intensity (`lite`, `full`, or `ultra`; default `full`): remove filler, pleasantries, hedging, narration of obvious steps, and prompt restatements.
2. Preserve technical artifacts 100% verbatim: code blocks, diffs, shell commands, file paths, line numbers, identifiers, version strings, error messages, and security or irreversibility warnings must remain exact.
3. When drafting a commit message, inspect the actual staged or working diff first so the message reflects verified behavioral changes rather than intent guesses.
4. Format an imperative Conventional Commit subject (`type(scope): summary` or `type: summary`) of at most 50 characters with no trailing period, using `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`, or `revert`.
5. Add concise body bullets only when needed to explain non-obvious rationale, migration impact, or breaking changes; keep `git` read-only unless the user explicitly authorizes staging or committing.

## Concrete boundary to investigate

Aggressive token compression shortens an identifier inside a code snippet or truncates a breaking-change warning, and an unverified commit subject claims `fix(auth): prevent token replay` when the diff only updated logging.

Use this example as a discovery lead, not evidence about an uninspected repository.
Read the actual diff and preserve every symbol, command, path, and safety warning verbatim.

## Deliver

Use the [handoff](assets/handoff.md) and [starting prompt](assets/prompt.md). Return
terse prose with exact technical spans and a diff-verified 50-character Conventional Commit
draft. Resolve related workflow skills by name, without assuming a host API.
Validate structured output artifacts using [terse output and commit schema](assets/terse-output-commit.schema.json), [worked examples](references/examples.md), and `python3 <this-skill>/scripts/validate_workflow.py <artifact.json>`.
Do not abbreviate code or identifiers, modify application source files, or run mutating git commit commands without explicit user authorization.
