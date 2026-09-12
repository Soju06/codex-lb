## Why

The scheduled sweep added by `retire-unroutable-bridge-continuity-owners` frees a thread six
hours after its last turn. That is the right window for a dormant thread, and the wrong one for
the turn a user is waiting on: when an operator pauses an account, every thread it owns keeps
returning 502 `previous_response_owner_unavailable` until the sweep catches up.

The owner's own state usually answers the question directly. A rate or quota limit carries
`accounts.reset_at`, a horizon saying when the owner comes back. `paused`, `reauth_required` and
`deactivated` carry no horizon at all — for those, waiting can never help. So the request can
decide for itself rather than waiting out a fixed window.

## What Changes

- **One narrower question, asked at the failure.** `DurableBridgeRepository.retire_continuity_owner_if_unavailable`
  retires a single row's owner when it cannot return before **this request's own budget** runs
  out. `reset_at` before the deadline means wait — that preserves the upstream prompt cache
  instead of forcing a full resend. `reset_at` after it, or absent, means retire.
- **Rebind inside the same request.** The HTTP bridge connect-failure path calls it once per
  request and, on success, re-enters its existing retry loop with the owner dropped. The turn is
  re-sent as `_http_bridge_payload_without_previous_response_id(untrimmed_effective_payload)` —
  the same anchor-free body shape the context-overflow and stale-anchor recoveries already use.
  The user sees a served turn, not a 502 followed by a retry.
- **Only a proxy-injected anchor.** A client that supplied its own `previous_response_id` named
  upstream state that lived on that account; dropping it here would silently change what the
  client asked for, so that shape keeps failing closed. A file-pinned request is likewise
  excluded. Every production trace of this bug carries `previous_response_id=None`, which is the
  shape this covers.
- **Same serialization as the sticky writer.** The CAS locks the account row with
  `SELECT ... FOR UPDATE` before the update, because PostgreSQL evaluates the status subquery
  from the UPDATE's snapshot: without the lock, a concurrent recovery can commit while the
  statement waits on the session row and the stale snapshot would still authorize a retirement.
  The status predicate stays inside the UPDATE as a second, database-level invariant.
- **A distinct marker, the same meaning.** The request path writes
  `continuity_abandonment_scope="request_path"` and leaves the timestamp NULL; the sweep writes
  the global timestamp form. Both read as retired, and a replica on a build without the columns
  keeps treating `account_id` as hard ownership through a rolling deploy.

No schema change — this uses the columns the previous change added. No new `CODEX_LB_*` setting:
the deadline is the request's existing budget. No ceiling-guarded proxy file grows.

## Not in this change

- **Owners resolved without a durable row.** When the pin came from the request-log index or the
  in-process registry there is no row to mark, so the turn still fails closed. That is the
  `no_durable_lookup` reason the attribution change reports, and it is its own fix.
- **Moving a turn whose body cannot leave its account.** Retirement stops the re-welding; it does
  not make encrypted reasoning or an uploaded file portable.

## Impact

- Affected specs: `sticky-session-operations` (ADDED — request-path retirement and its horizon
  rule).
- Affected code: `app/modules/proxy/durable_bridge_repository.py`,
  `app/modules/proxy/durable_bridge_coordinator.py`,
  `app/modules/proxy/_service/http_bridge/streaming.py`.
- Tests: `tests/unit/test_durable_bridge_owner_retirement.py` (seven cases covering the horizon
  rule, owner mismatch, idempotence and reversibility) and
  `tests/integration/test_http_responses_bridge.py` (a hard `thread_header` resume served on a
  healthy account with no sweep and no grace).
- Partial fix for #1707.
