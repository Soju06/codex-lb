# Settle Budget-Exhausted Compact Preflight Reservation

## Why

On the HTTP-bridge / forwarded compact path the caller passes an
`api_key_reservation_override` with `owns_reservation` false, so
`compact_responses` is the SOLE settler of the API-key usage reservation. PR
#1254 (`serialize-cross-replica-token-refresh`) closed the reservation leak for
the compact preflight's transport-failure, claim-contention, and
permanent-refresh terminal raises — each now settles the reservation via
`_settle_compact_api_key_usage` before raising. That audit explicitly left the
sibling budget-exhaustion terminals out of scope.

Those budget-exhaustion terminals (`_raise_proxy_budget_exhausted()` in the
compact request path — before/after the freshness-check preflight and before the
post-401 forced-refresh retry) raise a `ProxyResponseError(502,
upstream_request_timeout)` that propagates straight through the outer
`except ProxyResponseError` handler (which does NOT settle) and the `finally`
(which only writes a request log — it does NOT settle). Result: on the
sole-settler path the API-key reservation LEAKS held quota until it expires.
This is the same class of leak #1254 fixed for the transport/permanent siblings.

## What Changes

- At every budget-exhaustion terminal raise in the compact request path where an
  API-key reservation may be held and `compact_responses` is the sole settler,
  settle the reservation (release it via `_settle_compact_api_key_usage`) BEFORE
  `_raise_proxy_budget_exhausted()`, mirroring the neighboring
  transport/permanent/claim-contention branches. This covers the three
  preflight/retry terminals reached with an unsettled reservation: the
  before-freshness-check budget terminal, the after-freshness-check budget
  terminal, and the post-401 forced-refresh preflight budget terminal.
- The inner `_call_compact` budget terminals are deliberately left unchanged:
  their `ProxyResponseError(upstream_request_timeout)` is caught by the enclosing
  retry-loop `except ProxyResponseError` handler, which already settles the
  reservation on the `upstream_request_timeout` / account-neutral branches before
  raising. Adding a settle there would double-settle.
- Existing `release_account_lease` calls are preserved.
- Spec delta in `usage-refresh-policy` (completing the compact preflight
  reservation-settlement invariant for the budget-exhausted terminal); a
  route-level regression test on the compact responses harness.

## Impact

- Affected specs: `usage-refresh-policy`
- Affected code: `app/modules/proxy/_service/compact.py`
- Affected tests: `tests/integration/test_proxy_compact.py`
