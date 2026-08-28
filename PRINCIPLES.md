# codex-lb Founding Principles

codex-lb exists to be a proxy you can run in one command, with a dashboard
you can read in one glance. Every feature the project has gained since then
is welcome — but none of them may tax the first five minutes of a new
user's experience.

These principles are normative. Reviewers apply them as merge gates (see
[Simplicity gates](.github/CONTRIBUTING.md#simplicity-gates)); the
machine-checkable spec is `openspec/specs/contribution-simplicity/spec.md`
(created when the codify-simplicity-principles change is archived; until
then the delta spec lives under
`openspec/changes/codify-simplicity-principles/`) and this file is its
human-readable rendering.

## P1 — One-click setup is sacred

- New features MUST default to **off**, or to a zero-config working default.
- A PR MUST NOT add a new required setup step (env var, migration action,
  external account, manual file edit) to the base install path. If a change
  genuinely cannot avoid one, it needs explicit maintainer approval recorded
  on the PR via the `simplicity-budget-approved` label.
- `docker run` / `uvx codex-lb` with no env file MUST keep producing a
  working proxy and dashboard.

## P2 — Every new setting must justify not being a default

- A PR that adds a `CODEX_LB_*` setting or an `.env.example` line MUST
  answer "why can't this be a hardcoded default?" in the PR body (the PR
  template has a slot for it).
- Settings that only tune internals SHOULD stay out of `.env.example`.
  The documented-by-default configuration surface is budgeted (see P3).

## P3 — README and dashboard surface are budgeted

- README top-level sections, the `.env.example` surface, and dashboard
  core-nav items are capped. The concrete budget values live in
  `.github/simplicity-budgets.toml` — that file, not this one, is where
  numbers are set and changed.
- Exceeding a budget requires the maintainer-applied
  `simplicity-budget-approved` label on the PR before merge.
- Raising a budget value itself is a change to this contract and gets the
  same label plus an OpenSpec change.

## P4 — Features are documented in the docs site + OpenSpec, not the README

- New feature documentation goes to `docs/` (the user-facing rendering)
  and MUST link back to the owning `openspec/specs/<capability>/` entry,
  which stays the source of truth.
- A new README section is a budget exception under P3, not a documentation
  mechanism. The README exists to get a new user from zero to a running
  proxy — everything else belongs in the docs site.

## P5 — Dashboard-visible changes show their pixels

- Any PR that changes what the dashboard renders MUST include before/after
  screenshots (or a short screen recording) in the PR body.
- "It's a small CSS tweak" is not an exemption; small tweaks make small
  screenshots.

## Applying these principles

| Principle | What the reviewer checks | Where the gate lives |
|-----------|--------------------------|----------------------|
| P1 defaults-off | New feature works untouched with zero config; no new required setup step | CONTRIBUTING [Simplicity gates](.github/CONTRIBUTING.md#simplicity-gates); PR template "Simplicity" |
| P2 settings justified | PR body names each new setting and why it can't be a default | PR template "Simplicity" |
| P3 budgets | README sections, `.env.example`, dashboard core nav within `.github/simplicity-budgets.toml` | CI budget check (CI-enforced as of the `ci-simplicity-budgets` change; reviewer-enforced before that); `simplicity-budget-approved` label for exceptions |
| P4 docs placement | Feature docs land in `docs/` + OpenSpec, not new README sections | CONTRIBUTING [Simplicity gates](.github/CONTRIBUTING.md#simplicity-gates) |
| P5 screenshots | Before/after screenshots for dashboard-visible changes | PR template "Screenshots / output" |

Rationale, the erosion metrics that motivated codifying these rules, and a
worked example live in
`openspec/specs/contribution-simplicity/context.md` (change-level context
until the change is archived:
`openspec/changes/codify-simplicity-principles/context.md`).
