## 1. One predicate
- [x] 1.1 Widen the shared predicate to the union of the three sites' message sets and narrow it to the driver exception.
- [x] 1.2 Delete `_is_locked_error`, and both copies of `_is_sqlite_database_locked`, routing their call sites through the shared predicate.

## 2. One diagnostic
- [x] 2.1 Report `sqlite_errorname` on leader election's two best-effort shutdown swallows, without retrying either.
- [x] 2.2 Report `sqlite_errorname` on each retry (DEBUG) and on the exhausted budget (WARNING) of the API-key and refresh-claim loops, keeping their budgets and backoff unchanged.

## 3. Coverage
- [x] 3.1 Pin the union, the driver-exception narrowing and the give-up report in a shared unit suite.
- [x] 3.2 Cover each call site: the classification it had before and the error name reaching its log record.
- [x] 3.3 Pass lint, type check, migration check, the touched suites, and strict OpenSpec validation.
