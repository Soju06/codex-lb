## Purpose and scope

Auth Guardian proactively refreshes stale eligible account credentials without request traffic. This change repairs candidate handoff between two intentionally separate database-session scopes. It does not alter eligibility, cadence, batching, concurrency, leader election, token rotation, or account status behavior.

## Decision rationale

The scheduler needs only stable account IDs after candidate selection. Copying those scalar IDs before the query session closes preserves the existing ownership model: the query session selects work, and each refresh worker opens its own session to re-read current account state. Keeping the query session open across concurrent refreshes would lengthen transaction lifetime and risk sharing one `AsyncSession` across tasks.

## Constraints and failure modes

- A read query starts an implicit SQLAlchemy transaction. The background-session cleanup rolls that transaction back, which expires loaded attributes before close detaches the instances.
- Detached ORM instances cannot lazy-load expired attributes, including the primary key in this observed lifecycle.
- A failure to snapshot IDs inside the query scope aborts the entire pass before per-account isolation and backoff can apply.
- Each refresh worker must continue to re-read the account so eligibility and credentials are current at execution time.

## Concrete example

Given a stale paused account with ID `account-a`, candidate selection copies `account-a` while its query session is active. After that session closes, the worker receives the plain string `account-a`, opens its own repository session, re-reads the account, and performs the existing forced refresh path.

## Related contracts

- Normative delta: `specs/usage-refresh-policy/spec.md` in this change.
- Existing capability: `openspec/specs/usage-refresh-policy/spec.md`.
- Upstream report: `Soju06/codex-lb#1668`.
