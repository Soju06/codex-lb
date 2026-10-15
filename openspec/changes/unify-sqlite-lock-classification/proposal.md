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
  matched, and matches the driver exception rather than the rendered SQL — a
  wrapper carrying no driver exception is not contention either, since the only
  text left to match there is the statement and parameters just excluded.
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

### Unchanged Capabilities

- api-keys: *Requirement: API Key update* already requires that "a transient
  SQLite lock or snapshot conflict during the update MUST roll back and retry
  the complete read/build/write transaction, including rereading existing
  limits, before returning an error", with the scenario *Retry API-key PATCH
  after a transient SQLite snapshot conflict*. `update_key` is one of the sites
  rewired here, and the one whose haystack this change narrows most, so that
  requirement was re-read against the rewiring. The promise does not move and
  needs no delta: the rollback, the reread of existing limits, the recursive
  whole-transaction retry, the four-attempt budget and the eventual error are
  all untouched. Only which driver failures enter that branch changes, and both
  directions move the code toward the requirement rather than away from it —
  `database is busy` is a transient SQLite lock the old local predicate refused
  to retry (the requirement already demanded it), and a non-lock failure whose
  lock text lived only in the rendered statement or a bound parameter is not "a
  transient SQLite lock or snapshot conflict" and was never owed a retry.

## Impact

`app/db/sqlite_lock_retry.py` plus three call-site modules
(`app/core/scheduling/leader_election.py`,
`app/modules/api_keys/service.py`, `app/modules/accounts/refresh_claims.py`).
No configuration, schema, or request-path change; no retry budget or backoff
constant changes.
