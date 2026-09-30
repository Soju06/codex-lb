# Local verification — 2026-09-30

Baseline: published PR #2429 head `c4041d24`, integrated locally with upstream `main@f8ffbac20` without committing or pushing.

- Guardian, request refresh, dynamic multi-replica, and runtime background-job suites: **61 passed**.
- Coverage includes active/paused twelve-hour boundaries, independence from changed request freshness, fresh-row handoff, oldest-first 100-account admission with deferred account on the next pass, backoff, cancellation settlement, and a real persisted-account/credential-exchange background path preserving paused status.
- Background-job frontend file: **5 passed**.
- `make lint`, `uv run --frozen ty check`, migration topology (no new revisions), simplicity budgets, and strict follow-up validation: passed.
- All **67 stable OpenSpec capabilities** validate strictly.
- Older shared-eight-day proposal/context are explicitly historical and superseded; live main spec, stable context, and three descriptions retain twelve-hour keepalive.
- Independent read-only local review session `64361813-59ea-4e89-bc5e-a318debbfa32` completed with no concrete actionable defects. Supplied test results were not rerun by the reviewer.

No commit, push, cloud CI on a new head, merge, or production operation performed. The reworked PR still needs maintainer review; local checks are not permission to merge. Final local review is verified. The follow-up remains active for the reworked PR's maintainer review and publication.
