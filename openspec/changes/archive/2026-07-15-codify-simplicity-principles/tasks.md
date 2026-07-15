# Tasks

- [x] 1. Write `PRINCIPLES.md` at the repo root: preamble, P1–P5 as MUST-style reviewer-checkable rules, and an "Applying these principles" table; reference `.github/simplicity-budgets.toml` for budget values instead of hardcoding numbers.
- [x] 2. Add merge gate 6 ("Simplicity gates must pass") to the numbered gate list in `.github/CONTRIBUTING.md` and insert a `### Simplicity gates` subsection between `### Merge gates` and `### Collaborator rules`, linking `../PRINCIPLES.md` and `openspec/specs/contribution-simplicity/spec.md`.
- [x] 3. Update `.github/PULL_REQUEST_TEMPLATE.md`: add a `## Simplicity` section (checkboxes + delete-if-N/A comment), make `## Screenshots / output` explicitly REQUIRED for dashboard-visible changes, and add one simplicity item to the final checklist.
- [x] 4. Update `CLAUDE.md`: append a simplicity-gates bullet to "PR Readiness / Review Trapdoors", amend the Documentation & Release Notes rule (OpenSpec SSOT + `docs/` rendering with spec backlinks, no feature docs as README sections), and extend the merge-gates summary bullet with the simplicity gate.
- [x] 5. Author the `contribution-simplicity` spec delta (ADDED requirements mirroring P1–P5 with scenarios) and `context.md` (erosion metrics snapshot + worked example).
- [x] 6. Validation: grep the diff for a single consistent label name (`simplicity-budget-approved`); `openspec validate codify-simplicity-principles --strict` and `openspec validate --specs`.
