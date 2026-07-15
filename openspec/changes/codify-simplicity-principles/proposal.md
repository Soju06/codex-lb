# Change: codify-simplicity-principles

## Why

codex-lb started as a one-command proxy with a glanceable dashboard, and the contribution process has no gate that protects that shape. The erosion is measurable: the README has grown to 652 lines, `Settings` exposes 164 fields, and the dashboard ships 14 feature modules competing for navigation space (snapshot 2026-07-15; see `context.md`). Each addition passed review individually because no principle said "the default experience is budgeted" — reviewers had nothing to point at.

## What Changes

- Add `PRINCIPLES.md` at the repo root: five normative, reviewer-checkable rules (P1 defaults-off/zero-config, P2 every new setting justifies not being a default, P3 budgeted README/config/dashboard surface, P4 feature docs go to docs/ + OpenSpec instead of new README sections, P5 screenshots for dashboard-visible PRs).
- Add merge gate 6 ("Simplicity gates") and a `### Simplicity gates` subsection to `.github/CONTRIBUTING.md`.
- Add a "Simplicity" section to `.github/PULL_REQUEST_TEMPLATE.md`, make the Screenshots section required for dashboard-visible changes, and add a simplicity line to the final checklist.
- Amend `CLAUDE.md`: a simplicity-gates trapdoor bullet, an updated documentation rule (OpenSpec stays the SSOT; user-facing rendering lives under `docs/` and links back to the owning spec), and a merge-gates summary that includes the new gate.
- Introduce the `simplicity-budget-approved` label as the single maintainer-applied override for budget exceptions (label creation on GitHub is an operator action; all documents reference this exact name).
- Budget numbers are NOT set here: they live in `.github/simplicity-budgets.toml`, introduced by the CI enforcement change (`ci-simplicity-budgets`). Until that lands, the simplicity gates are reviewer-enforced.

## Capabilities

### New Capabilities

- `contribution-simplicity`: the normative contract for keeping codex-lb's default experience simple — defaults-off features, justified settings, budgeted README/config/dashboard surface, docs-site documentation placement, and screenshot evidence for dashboard changes.

### Modified Capabilities

None.

## Impact

- Code: none (documentation and process only).
- Docs: `PRINCIPLES.md` (new), `.github/CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `CLAUDE.md`.
- Specs: new `openspec/specs/contribution-simplicity/spec.md` (via this change's delta).
- Operator action: create the `simplicity-budget-approved` label on GitHub before the first budget exception is needed.
- Follow-ups: `docs-site` (docs/ rendering + README diet), `ci-simplicity-budgets` (machine enforcement + `.github/simplicity-budgets.toml`), `dashboard-progressive-disclosure` (core vs advanced nav).
