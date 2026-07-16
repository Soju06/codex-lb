# Design: settle-budget-exhausted-compact-preflight

## Context

`compact_responses` (`app/modules/proxy/_service/compact.py`) is the sole settler
of the API-key usage reservation on the HTTP-bridge / forwarded path, where the
caller passes an `api_key_reservation_override` with `owns_reservation` false.
Every terminal exit that holds an unsettled reservation must therefore call
`_settle_compact_api_key_usage` before raising, or the reservation leaks held
quota until the stale-reservation reaper expires it. PR #1254 established this
invariant for the refresh-related preflight terminals; this change extends it to
the budget-exhausted terminals, which #1254 flagged out of scope.

## Decisions

### 1. Which budget-exhausted raises actually leak

`_raise_proxy_budget_exhausted()` raises `ProxyResponseError(502,
openai_error("upstream_request_timeout", ...))`. The compact path has budget
terminals in two structural positions:

- **Outer-loop preflight terminals** (before the freshness check, before the
  freshness reserve, after the freshness check) and the **post-401 forced-refresh
  preflight terminal** inside the retry loop's 401 handler. These raise straight
  out of `compact_responses` to the outer `except ProxyResponseError` handler
  (which re-raises without settling) and the `finally` (which only writes a
  request log). At each of these points a reservation may be held and no settle
  has run yet, because every in-loop settle site is immediately followed by a
  terminal `raise` — reaching a budget terminal means no prior settle fired.
  **These leak → settle before raising.**

- **Inner `_call_compact` terminals** (before the upstream call, after admission
  waits, before the upstream-budget cap). `_call_compact` is invoked only from
  inside the retry loop's `while True`. Its `ProxyResponseError(upstream_request_timeout)`
  is caught by the enclosing `except ProxyResponseError as exc:` handler, which
  routes an `upstream_request_timeout` (and account-neutral) code through
  `_settle_compact_api_key_usage(response=None)` BEFORE raising. The
  budget-exhausted error is non-retryable (`retryable_same_contract` defaults to
  False), so it does not loop indefinitely; it settles and surfaces.
  **Already covered → left unchanged to avoid double-settling.**

### 2. No double-settle

`_settle_compact_api_key_usage` releases (or finalizes) the reservation and is
guarded internally (`api_key is None or api_key_reservation is None` short-circuit,
and the release/finalize DB writes are keyed by `reservation_id`). More
importantly, the control flow guarantees at most one settle per call on the
leaking paths: every settle site is terminal (followed by `raise`), so a
budget terminal is only ever reached with the reservation still unsettled. The
added settles therefore run exactly once.

### 3. Preserve escalation and lease release

Each fixed site keeps its existing `release_account_lease(...)` call and still
raises the same `ProxyResponseError(502, upstream_request_timeout)` via
`_raise_proxy_budget_exhausted()` after settling, so external status/escalation
behavior is unchanged — only the reservation is now finalized instead of leaked.

## Risks / Trade-offs

- Settling on `response=None` releases the reservation (no usage recorded), which
  is correct: a budget-exhausted request never reached upstream, so no tokens
  were consumed.
- The regression asserts the settle call site is reached (mirroring the existing
  #1254 transport/permanent tests), which pins the invariant at the externally
  failing product path (the `/backend-api/codex/responses/compact` route).
