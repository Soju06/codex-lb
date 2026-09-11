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
  `server_error` **stream terminal**, keyed on the absence of an upstream HTTP
  status because the terminal shape is what makes it an admission rejection. A
  coded HTTP 5xx carrying the same string stays an ordinary transient error,
  and an HTTP 429 carrying it keeps its existing
  `record_upstream_burst_rejection` branch.
- **Soft-gate-only status evidence.** "No `http_status` argument" is an
  *inference* about the caller, not a fact it asserted, and three call sites
  break it: they write health from a settled failure or a terminal frame whose
  upstream HTTP status they know but deliberately do not forward. Forwarding it
  positionally is not available to them, because the positional `http_status`
  also drives four unrelated decisions — it flips
  `_is_account_neutral_request_rejection` and `_is_model_scoped_rejection` from
  "skip the penalty" to "record it", arms the `http_status == 429` burst
  cooldown, and double-counts `codex_lb_upstream_reasoning_replay_400_total`
  for a frame already counted at `_observe_terminal_stream_error_frame`. So
  `_handle_stream_error` gains a keyword-only `upstream_http_status` read by
  the soft-overload gate and nothing else, and the gate becomes
  `code in UPSTREAM_SOFT_OVERLOAD_CODES and http_status is None and
  upstream_http_status is None`. Every other decision keeps byte-for-byte its
  previous inputs.
- **Call sites that now report their status.** The mid-stream
  downstream-visible terminal renderer and the outer `ProxyResponseError`
  terminal renderer in `_service/streaming/retry.py`, and the WebSocket /
  HTTP-bridge settlement write in `_service/websocket/mixin.py`, where the
  bridge already parsed the frame's upstream status into
  `error_http_status_override`. `_finalize_terminal_settlement_after_downstream_close`
  also takes the keyword, so a downstream disconnect mid-failure-frame reports
  the same status as the non-cancelled path (`_StreamSettlement` carries no
  HTTP-status field of its own).
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

## Call sites deliberately left alone

The funnel has 28 call sites. Twelve already forward `http_status`. Of the
sixteen that do not, all but the three above have a bounded code vocabulary
that provably excludes `server_error` — a literal `upstream_unavailable`
(`_service/support.py`), `_ACCOUNT_RECOVERY_RETRY_CODES` (the
previous-response rewrite in `_service/streaming/mixin.py`), the
quota / `server_is_overloaded` / `stream_incomplete` / model-unsupported
`retry_error_code` set (the WebSocket and bridge retry paths), or a
connection-level transport code. The two remaining ones
(`_service/streaming/retry.py`, the post-`_stamp_terminal` renderers) are the
genuinely status-less SSE terminals this feature exists to catch.

One nearby call site *looks* like a fourth offender and is not: the
`ordered_settlement_required` write that follows the forced-refresh retry is
reached only when the post-refresh stream returned **normally**, so its
settlement was populated by a `_TerminalStreamError` — an HTTP 200 stream that
died on a terminal frame, i.e. exactly the shape the window is for. The only
`ProxyResponseError` in scope there is the original HTTP 401 that triggered the
refresh, and reporting that status would silently disable the feature on the
whole post-refresh path.

## Known coverage gap (false negative, unchanged by this PR)

The deferred-penalty queue in `_service/streaming/retry.py` records a
`_TransientStreamError` with a hardcoded `502` as its `http_status`. A
`_TransientStreamError` wrapping a connect-phase or capacity failure can carry
`.code == "server_error"` and is genuinely status-less, but the hardcoded 502
keeps it out of the window. Real coverage is therefore narrower than the
headline claim; widening it needs the queue to carry the true status and is
left for a follow-up.

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
  `_service/streaming/helpers.py`, `_service/streaming/retry.py`,
  `_service/websocket/mixin.py`, `_service/support.py` (the one Protocol that
  mirrors the full keyword-only signature).
- Tests: `tests/unit/test_overload_backoff.py` (10 added).
