## Why

`app/db/sqlite_lock_retry.py` was introduced as the shared home for "is this
SQLite write-lock contention", but three sites still hand-rolled their own
substring match, and they had already drifted apart:

- leader election matched `database is locked` / `database is busy`;
- the API-key usage-reservation writes additionally matched SQLITE_LOCKED's
  `database table is locked` / `database schema is locked` and a
  `busy_snapshot` result-code name;
- the refresh-claim upsert matched only `database is locked`.

So the same contention was transient in one module and fatal in another, and
two of the three matched against `str(OperationalError)` — which renders the
failing statement and its bound parameters as well as the driver message, so
an unrelated failure on a statement whose parameter happened to contain the
lock text was silently retried.

None of the three logged the driver's `sqlite_errorname`, so a production or
CI occurrence still cannot say whether the slot was lost instantly
(`SQLITE_BUSY_SNAPSHOT`, where the retry is the whole fix) or only after the
full busy timeout (`SQLITE_BUSY`, where a foreign writer held the slot and the
retry budget is beside the point).

## What Changes

- Route all three remaining sites through the shared predicate and delete the
  duplicated helpers; the predicate now matches the union of what the sites
  matched, and matches the driver exception rather than the rendered SQL.
- Report `sqlite_errorname` wherever a lock failure is retried, swallowed, or
  given up on, so the next occurrence classifies itself.
- Change no control flow: leader election's two best-effort shutdown lease
  writes keep failing fast (their next cadence is the retry, and the release
  is one-shot), and the API-key and refresh-claim loops keep their own attempt
  count, their own exponential backoff and their own re-raise.

## Capabilities

### Added Capabilities
- database-backends: one shared classification, and one named diagnostic, for
  SQLite write-lock contention.

### Modified Capabilities
- scheduler-coordination: the best-effort shutdown lease writes classify
  contention with the shared predicate, keep failing fast by design, and name
  the driver result code in their debug report.

## Impact

`app/db/sqlite_lock_retry.py` plus three call-site modules
(`app/core/scheduling/leader_election.py`,
`app/modules/api_keys/service.py`, `app/modules/accounts/refresh_claims.py`).
No configuration, schema, or request-path change; no retry budget or backoff
constant changes.
