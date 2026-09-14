## Why

A single pooled account's HTTP 429 is not the pool's 429, but today it is
returned to the client as one.

`failover_decision` (`app/core/balancer/logic.py`) stops the walk on
`candidates_remaining <= 0`, and every caller derives that number from a fixed
per-transport constant — `_STREAM_MAX_ACCOUNT_ATTEMPTS = 3`,
`_WEBSOCKET_MAX_ACCOUNT_ATTEMPTS = 3`, `_COMPACT_MAX_ACCOUNT_ATTEMPTS = 2`
(`app/modules/proxy/service.py`) — that has no relationship to how many usable
accounts the pool actually holds. On a 28-account fleet a request gives up
after three. The failure that is then surfaced is the *first rejecting
account's* verbatim upstream 429 (`_service/streaming/retry.py`, the bare
`raise` after `_handle_stream_error`), so a client sees a limit that belongs to
one account as though it were the limit of the whole pool.

Two message-classification gaps make this worse:

1. A rejection whose message says the *selected model is at capacity* describes
   the model, not the account. The existing requirement "Model-capacity messages
   are retryable transient failures" already decided, deliberately, that a quota
   or rate-limit **code** keeps its stronger classification for account health
   even when that message is present, and that the model-capacity replay wait
   still applies. That decision stands. What is missing is a separate question:
   *does this rejection justify excluding the account for the rest of the
   request?* Today nothing asks it, so once the walk is unbounded a capacity
   rejection would rotate the whole pool for a condition no account can serve.
2. The mirror case is a live bug. A code-less 429 — or an
   `invalid_request_error` — whose message asserts the usage limit normalizes
   to `upstream_error` and classifies `retryable_transient`, which makes
   `is_upstream_burst_rejection` true. The proxy then applies bounded
   same-account backoff **to an account that is actually out of quota**, waits
   1 s / 2 s / 4 s against a wall, and surfaces the rejection.

The pool-exhaustion answer already exists and is already specified:
`probe_pool_usage_exhaustion` (`app/modules/proxy/_load_balancer/exhaustion_probe.py`)
asks the real selector the same question ordinary selection asks, and
"Pool usage exhaustion is reported as a usage-limit error" already mandates the
`usage_limit_reached` 429 with `error.resets_at`. Nothing reaches it on this
path, because the walk surfaces the first account's error long before the pool
is exhausted.

## What Changes

- **One classifier, one richer answer.** `classify_upstream_failure` keeps
  producing today's `failure_class` for every envelope that already carries a
  code — no existing classification is inverted — and additionally reports
  whether the walk may move off this account. Account selection reads that
  answer; account health keeps reading `failure_class`. It is true for every
  walkable class and false only for a model-capacity rejection whose health
  write leaves the account selectable; a rate-limit or quota code still benches
  and excludes the account. A usage-limit message additionally raises the class
  from `retryable_transient` to `rate_limit`, but only for the two codes that
  carry no classification decision of their own. No second classifier is
  introduced.
- **The walk is bounded by the pool, not by a constant.**
  `failover_decision` takes `more_candidates_possible: bool` instead of
  `candidates_remaining: int`, and the `non_retryable` check moves ahead of the
  budget check so the decision log distinguishes "the request was bad" from
  "we ran out of accounts". The three per-transport attempt constants are
  deleted. Termination is proved by three independent bounds: the existing
  request deadline, a candidate-count-derived runaway fence, and a
  monotone-progress invariant — a `failover_next` that does not grow
  `excluded_account_ids` logs `pool_walk_no_progress` and terminates.
- **The client-visible failure is decided at the end of the walk**, by a new
  single-purpose `app/modules/proxy/pool_terminal.py` that asks
  `probe_pool_usage_exhaustion` at most once, except for non-retryable and
  exhausted-budget exits, whose prescribed responses bypass the probe.
  Exhausted pool -> the canonical `usage_limit_reached` 429 with
  `error.resets_at`. Any other eligible answer, including the drain-strategy
  decline -> the preserved last per-account failure, verbatim.
- **Owner-bound requests stay non-relocatable.** They return at the
  `owner_bound` branch of `failover_decision` before the walk is reached.
  Burst 429s keep the bounded same-account backoff; code-less or
  `invalid_request_error` usage-limit messages still change classification and
  skip same-account backoff before surfacing on the owner. Required
  previous-response-owner compact requests remain eligible for their existing
  account-neutral fresh-replay path when those gates prove safe relocation.
- Exactly one account-health write per attempted account per request.

## Impact

- Affected capabilities: `account-routing` (three ADDED, one MODIFIED),
  `responses-api-compat` (two ADDED, one MODIFIED), `usage-refresh-policy`
  (one MODIFIED — the immediate-refresh trigger becomes the classification
  rather than the literal `usage_limit_reached` code, without widening to
  throttling or quota codes).
- **Clients** that today receive one account's 429 while other accounts are
  usable now receive a served response. An unbound request receives the
  canonical exhausted-pool 429 with `error.resets_at` only when the
  pool-exhaustion probe or the walk's own usage-window exhaustion evidence
  confirms exhaustion. Other terminal paths preserve their existing failure
  contract.
- **Operators**: no new setting. The runaway fence is derived from the current
  candidate count, not from a fixed operator knob. The `[settings_fields]`
  ratchet does not move.
- **Upstream load**: a request that previously stopped at three accounts may
  now attempt more. It is bounded by the same request deadline as before, and
  each attempt is a request that would otherwise have been a client-visible
  failure plus a client retry.
- `tests/unit/test_failover_foundation.py::test_rate_limit_code_takes_precedence_over_capacity_message`
  and the spec scenarios "Quota and rate-limit codes retain their stronger
  classification" and "Classified quota failures still use the model-capacity
  replay wait" keep passing unchanged: this change adds an account-exclusion
  answer beside the classification rather than reordering the classification.
