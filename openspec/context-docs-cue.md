# OpenSpec Context Docs Cue

Use this cue when writing OpenSpec documentation to keep SSOT clean and add rich context without breaking the spec.

Prompt cue:

"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

Suggested detail prompts (pick 4+):
- Purpose / scope / non-goals
- Decision rationale + alternatives considered
- Constraints (security, performance, policy)
- Failure modes / edge cases
- Example request/response, data shape, or user flow
- Operational notes (rollout, monitoring, runbooks)
- Links to related specs/contracts
