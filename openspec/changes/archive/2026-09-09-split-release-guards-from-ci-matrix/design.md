## Context

`ci.yml` is one workflow with a per-ref concurrency group and
`cancel-in-progress: true`. Two of its jobs (the beta and stable release guards)
depend on pull-request metadata: the beta guard validates a checklist and head
SHA recorded in the release PR body, and both check the head branch name. Every
other job depends only on the head tree. GitHub fires `pull_request: edited` for
title, body, and base changes.

## Goals / Non-Goals

**Goals:**

- A PR title/body edit never cancels or restarts the CI matrix.
- Release-evidence edits are still revalidated automatically.
- Every check context name stays the same.

**Non-Goals:**

- Changing the branch ruleset or which checks are required.
- Changing what the guards validate.
- Deduplicating same-head runs caused by `synchronize` (a real push must run).

## Decisions

- **Remove `edited` from `ci.yml` rather than skip jobs on `edited`.** A
  `github.event.action != 'edited'` condition on heavy jobs would still create a
  new run whose skipped jobs report success; branch protection reads the newest
  check run per context, so a body-only edit could turn a red head green. Not
  triggering at all leaves the head's existing check runs authoritative. This
  is the hazard the old `ci.yml` header warned about, and the split honors it.
- **Separate workflow for the guards, mirroring `simplicity-budgets.yml`.** That
  workflow already isolates `labeled`/`unlabeled` from the matrix for the same
  reason. The guards are stdlib-only and finish in seconds, so re-running them
  on every metadata edit is free.
- **Keep `push`/`merge_group` on the guard workflow.** The guards are no-ops
  without a `pull_request` payload, so this costs two trivial jobs per push, but
  it keeps parity with the old placement and lets a maintainer add the contexts
  to the ruleset (GitHub cannot require a context that has never reported).
- **`CI Required` loses the beta guard.** Cross-workflow `needs` is impossible.
  Neither `CI Required` nor the guards are ruleset-required today, so this
  removes no enforcement; the publish-time guard stays the hard gate for tags.
  If a pre-merge hard gate is wanted, require `Beta release guard` directly.
- **Pin the split with a unit test.** `tests/unit/test_ci_workflow_required_checks.py`
  already asserts structural properties of `ci.yml`; two new tests assert the
  trigger sets and job placement so the regression is machine-caught.

## Risks / Trade-offs

- [Risk] A release PR whose body is edited to add evidence no longer gets a
  fresh `CI Required` run. → `CI Required` never depended on the body except via
  the guard; the head's matrix result is unchanged by an edit, and the guard
  re-runs on its own.
- [Risk] Base-branch retarget (`edited` with `changes.base`) no longer re-runs
  the matrix. → Retargets are rare here (release PRs target `main`); a push or
  manual re-run covers it, and the merge queue still runs the full suite.
