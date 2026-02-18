---
name: openspec-context-docs
description: "OpenSpec context documentation policy: keep requirements in spec.md and capture narrative context in context/overview/rationale docs under openspec/specs. Use when creating or updating OpenSpec docs, spec context, or onboarding/guide documentation."
---

# OpenSpec Context Docs

## When to use

- Writing or updating OpenSpec documentation (specs, onboarding, guide, change artifacts).
- A spec is complete but readers lack background, rationale, examples, or operational notes.
- You need to preserve rich context without weakening SSOT.

## Core rule: two-layer docs

1) `spec.md` = normative, testable requirements (SHALL/MUST + scenarios).
2) Context docs = narrative and operational detail that helps humans understand and apply the spec.

## Locations

- Main specs:
  - `openspec/specs/<capability>/spec.md` (requirements)
  - `openspec/specs/<capability>/context.md` (preferred)
- Optional split (if context grows):
  - `overview.md`, `rationale.md`, `examples.md`, `ops.md` inside the same capability folder.
- Change-level notes (working context):
  - `openspec/changes/<change>/context.md` or `notes.md`

## Detail prompts (add depth without forcing a template)

Include at least 4 of these in context docs:
- Purpose / scope / non-goals
- Decision rationale + alternatives considered
- Constraints (security, performance, policy)
- Failure modes / edge cases
- Example request/response, data shape, or user flow
- Operational notes (rollout, monitoring, runbooks)
- Links to related specs/contracts

## Sync rule

- After implementation/verification, promote stable context from change notes into the main context docs.
- Do not duplicate normative requirements in context docs; link back to `spec.md`.
- Never create or update a `docs/` directory; keep everything under `openspec/`.

## Prompt snippet

"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."
