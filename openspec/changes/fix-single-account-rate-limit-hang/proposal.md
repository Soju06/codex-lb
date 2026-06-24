## Why

`add-account-capacity-wait-streaming` (PR #1000, merged in v1.20.0) introduced
a capacity-wait loop in `app/modules/proxy/_service/websocket/mixin.py` and
`app/modules/proxy/_service/http_bridge/streaming.py` that retries
`_select_account_with_budget_compatible` after sleeping for the duration
embedded in the selector's error message. The recovery hint is extracted by
`_account_selection_recovery_sleep_seconds_from_message` in
`app/modules/proxy/_service/support.py`, which currently matches
`try again in <N>s` against any error message and treats every such message
as recoverable.

The proxy's own `select_account` formats its no-account error using exactly
that shape via `_format_retry_hint` in `app/core/balancer/logic.py`:

```python
def _format_retry_hint(wait_seconds: float) -> str:
    capped = min(max(0.0, wait_seconds), float(SELECTOR_RETRY_HINT_MAX_SECONDS))
    return f"Rate limit exceeded. Try again in {capped:.0f}s"
```

Once every eligible account is `RATE_LIMITED`, the loop becomes
self-sustaining: `select_account` returns "Rate limit exceeded. Try again in
300s", the capacity-wait loop matches its own message, waits the maximum
300 seconds, retries selection, and gets the same message back. The request
budget eventually exhausts, but each iteration can hold a request for up to
5 minutes.

For single-account deployments, every incoming request enters this loop
the moment the only account flips to `RATE_LIMITED`, and clients see
indefinite hangs until the operator restarts the process. The bug is also
reachable in multi-account pools whenever every eligible account is
simultaneously rate-limited.

Reported in [#1078](https://github.com/Soju06/codex-lb/issues/1078) with a
single-account reproduction on `v1.20.1`, root-caused to PR #1000, and
mitigated with a one-line skip of the locally-generated hint shape. This
change formalizes that skip and makes it part of the
`responses-api-compat` contract so a future capacity-wait expansion cannot
silently reintroduce the loop.

## What Changes

- Add a `responses-api-compat` requirement that locally-generated account
  selection retry hints (the `_format_retry_hint` shape used by
  `select_account`) MUST short-circuit the capacity-wait recovery path.
- Update `_account_selection_recovery_sleep_seconds_from_message` to return
  `None` when the message starts with the locally-generated
  `Rate limit exceeded. Try again in` prefix, alongside the existing
  permanent-failure allowlist.
- Preserve the existing handling of genuine upstream-derived recovery
  messages (workspace spend cap, future upstream retry hints) by keeping
  the prefix match narrow.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: streaming Responses capacity-wait recovery
  treats locally-generated selector retry hints as permanent for the
  current request budget instead of looping back into selection.

## Impact

- **Code**: `app/modules/proxy/_service/support.py`
- **Tests**: `tests/unit/test_proxy_utils.py`
- **Behavior**: single-account deployments restore the pre-v1.20.0
  fail-fast path on upstream 429 instead of hanging every subsequent
  request for up to one request-budget worth of capped retry hints.
  Multi-account deployments fail fast at the moment all eligible accounts
  are simultaneously rate-limited instead of waiting through the cap.
- **Specs**: `openspec/specs/responses-api-compat/spec.md` (additive
  requirement; no existing scenario removed).
