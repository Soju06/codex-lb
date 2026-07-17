# Context: contribution-simplicity

Normative requirements live in [`spec.md`](./spec.md). This document carries the
rationale, decisions, and operational notes behind the simplicity merge gates.

## Purpose

codex-lb's original value proposition was "run one command, get a load-balancing
proxy and a dashboard you can read in one glance." Nothing in the contribution
process defended that proposition: every merge gate checked correctness (CI,
codex review, OpenSpec coverage) and none checked whether the default experience
got heavier. This capability codifies five principles (P1–P5 in `PRINCIPLES.md`)
as reviewable merge gates so simplicity regressions are blocked the same way
broken tests are.

## Erosion metrics (snapshot 2026-07-15, main @ fd529b21, pre-effort)

| Surface | Measured | Note |
|---------|----------|------|
| `README.md` | 652 lines | Single-page docs for every feature; new-user path buried mid-file |
| `Settings.model_fields` (`app/core/config/settings.py`) | 164 fields | Each individually reasonable; collectively an un-navigable config surface |
| `.env.example` | 115 lines | Includes values that drift from code defaults |
| Dashboard feature modules (`frontend/src/features/`) | 14 modules | All compete for primary navigation space |
| Dashboard primary nav (`NAV_ITEMS`, `app-header.tsx`) | 6 items | No distinction between core and advanced destinations |

None of these numbers were decided; they accreted. The principles do not roll
them back by themselves — the companion changes did (`docs-site` dieted the
README and `.env.example`, `dashboard-progressive-disclosure` split core from
advanced nav) — but they stop the counters from silently climbing again.

## Decisions

- **One override label: `simplicity-budget-approved`.** A single, colon-free,
  space-free name is safe in `gh` CLI quoting and CI label JSON, and every
  document (PRINCIPLES, CONTRIBUTING, PR template, CLAUDE.md, the CI check)
  references the same string. Rejected: a namespaced variant containing a colon
  and space (quoting hazards, and a second name for the same gate).
- **No budget numbers in PRINCIPLES.md.** Concrete caps live only in
  `.github/simplicity-budgets.toml` (enforced per the `github-automation`
  capability) so prose and enforcement cannot drift. PRINCIPLES.md references
  the file by path.
- **New capability rather than extending an existing one.**
  `release-management`, `github-automation`, and `deployment-installation` each
  own adjacent machinery, but none owns the contribution/review contract. The
  CI enforcement lands under `github-automation` and references this
  capability.
- **Documentation rule amended, not inverted.** OpenSpec stays the normative
  SSOT. User-facing rendering lives under `docs/`, and each spec-governed page
  must link back to its `openspec/specs/<capability>/` entry. README sections
  stopped being a documentation target.

## Constraints

- The `simplicity-budget-approved` label must be created on GitHub by an
  operator (`gh label create simplicity-budget-approved ...`); repository
  labels are not versioned in-repo.
- The PR template must not itself bloat: the Simplicity section is capped at
  four checkbox/prompt lines plus a delete-if-N/A comment.

## Failure modes

- **Label drift**: if a second override label name ever appears, CI and
  reviewers enforce different gates. Mitigation: this context names the single
  label; a repo-wide grep for any other override-label spelling should return
  nothing.
- **Budget drift**: numbers quoted in prose go stale. Mitigation: numbers exist
  only in `.github/simplicity-budgets.toml`.
- **Gate fatigue**: contributors rubber-stamping the Simplicity checklist.
  Mitigation: the CI budget check makes the highest-traffic budgets
  (README/env/nav) machine-verified regardless of checkbox state.

## Worked example (P2)

A PR adds `CODEX_LB_FOO_TIMEOUT_SECONDS` to tune an upstream call. The PR body
must answer "why can't this be a default?":

> Acceptable: "Operators behind corporate proxies see 30–120s handshake times;
> no single default works for both LAN and proxied deployments. Default stays
> 8s; the setting is not added to `.env.example` because it only matters for
> proxied networks (documented in docs/troubleshooting.md, which links back to
> openspec/specs/upstream-proxying/)."
>
> Not acceptable: "Makes the timeout configurable." — that restates the diff.
> The reviewer blocks until the PR either justifies the knob or hardcodes a
> better default.

If the same PR also added the setting to `.env.example`, it would count against
the `env_example` budget in `.github/simplicity-budgets.toml`, and pushing that
file over its cap would additionally require the `simplicity-budget-approved`
label.
