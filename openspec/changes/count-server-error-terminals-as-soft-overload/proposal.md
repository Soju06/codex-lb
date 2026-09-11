## Why

Fixes #2348.

`record_upstream_overload` is fed only by `UPSTREAM_OVERLOAD_CODES`
(`server_is_overloaded`, `overloaded_error`). When upstream refuses an admitted
turn with a bare `server_error` stream terminal instead, the account never
accumulates a rejection, never trips the window, and never reaches the
isolation stage, so fresh admissions and soft sticky owners keep landing on it.

Both codes already classify as `retryable_transient`
(`app/modules/proxy/helpers.py::_TRANSIENT_CODES`), and both arrive the same
way: HTTP 200, a normal `response.created` frame, then the stream dies with a
bare upstream error. The only thing separating them is membership in
`UPSTREAM_OVERLOAD_CODES`.

On one production deployment `server_error` went from tens per day to **2,347
in a single day**, 1,483 of them in one 6-hour window. None contributed to
isolation. Isolation logs showed 17 accounts engaged while the sticky reroute
line still reported `overload_free_candidates=12`, i.e. the balancer counted
accounts as overload-free that were in practice refusing nearly everything.

## What Changes

- **New soft class.** `UPSTREAM_SOFT_OVERLOAD_CODES = {"server_error"}` and
  `SOFT_OVERLOAD_TRIP_WEIGHT = 0.5` in
  `app/modules/proxy/_load_balancer/overload_backoff.py`.
- **Second, fractionally weighted window.** `RuntimeState` gains
  `soft_overload_rejections` alongside `overload_rejections`.
  `record_overload_rejection_locked` takes `soft: bool = False`, appends to the
  matching window, and trips when
  `len(hard) + len(soft) * SOFT_OVERLOAD_TRIP_WEIGHT >= OVERLOAD_TRIP_COUNT`.
  A lone `server_error` fault therefore can never trip the window; six inside
  the 120-second window do, and the two classes combine naturally. Both windows
  are pruned by the same 120-second horizon and cleared together on a trip.
- **Funnel wiring.** `_handle_stream_error` records a soft observation for a
  `server_error` **stream terminal**. An HTTP 429 carrying the same code is a
  burst rejection and keeps its existing `record_upstream_burst_rejection`
  branch unchanged — the soft path is gated on `http_status != 429`.
- The backoff deadline, exponential level, decay, isolation trip level,
  soft-reroute semantics, failure classification, failover decision and the
  status and body returned to the client are all unchanged.

Replica-local runtime state only: no migration, no new `CODEX_LB_*` setting, no
`.env.example` change, no dashboard column, no new metric.

## Why a fractional weight rather than a second threshold

`server_error` genuinely covers one-off upstream faults as well as sustained
capacity refusal, so promoting it to a full-weight rejection would let a single
hiccup deprioritize a healthy account. A fractional weight in the *same* window
keeps one deadline, one level and one decay path, and makes the mixed case
(some explicit, some bare) behave sensibly without a second set of tunables.

## Deferred (documented, not implemented)

- **Shape-based accounting.** Keying on "admitted turn that produced zero
  output tokens and ended with an upstream 5xx-class error" instead of on the
  code string would be more robust, but the funnel has 25+ call sites and does
  not carry the produced-output signal today. Revisit if `server_error`
  rewrites appear for codes outside this set.
- **Per-code isolation counters** in diagnostics (#2349 territory).

## Impact

- Specs: `account-routing` (MODIFIED: overload rejection requirement).
- Code: `_load_balancer/overload_backoff.py`, `_load_balancer/types.py`,
  `_load_balancer/opportunistic_admission.py`,
  `_service/streaming/helpers.py`.
- Tests: `tests/unit/test_overload_backoff.py` (6 added).
