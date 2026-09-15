# Change: preserve-upstream-quota-terminals

## Why

Two terminal paths discard what upstream already told the client.

The post-refresh transient terminal in
`app/modules/proxy/_service/streaming/retry.py` built its `response.failed`
event with `response_failed_event(exc.code, error_message, ...)`, whose
`resets_at` defaults to `None`, even though the same exception carries the
numeric reset timestamp parsed out of the upstream error envelope. A client
that reaches this terminal sees the quota code but not when the window reopens.
The other two post-refresh terminals were already migrated; this one was
missed, so the same class of failure reported reset metadata inconsistently
depending on which branch rendered it.

The HTTP responses bridge rewrote **every** failed pre-created replay into a
synthetic 502 `stream_incomplete`, including the case where upstream had
already answered with a status-bearing 429 quota envelope. That replaced a
deterministic, actionable answer with a generic transport failure and dropped
`plan_type`, `resets_at`, and `resets_in_seconds` on the floor.

## What Changes

- The post-refresh transient terminal renders through the reset-preserving
  helper, so a quota error surfaced after exhausted same-account retries keeps
  its `resets_at`.
- A failed pre-created bridge replay preserves the original terminal when that
  terminal is a status-bearing 429 carrying a quota or rate-limit code:
  the client keeps the real code and the reset metadata, and the sticky
  error-override fields are cleared so no synthetic 502 is layered on top.
- Every other failed replay keeps failing closed as a synthetic 502
  `stream_incomplete`, because a terminal that carries no status-bearing quota
  answer proves nothing about the account.

No setting, schema, migration, or general failover behavior changes. The
same-account retry budget, account-health penalties, and selection order are
untouched.

### What this deliberately does not do

- **It does not widen which codes are transparent-replayable.** The preserved
  set is exactly the quota and rate-limit codes the bridge already classifies,
  and the terminal must still carry an upstream 429 status.
- **It does not make account health depend on the preserved terminal.** Health
  handling keeps its existing inputs; only the client-visible payload and the
  status override change.
- **It does not add a fallback for status-less terminals.** A replay that fails
  without a status-bearing quota answer stays masked.

## Impact

- Code: `app/modules/proxy/_service/streaming/retry.py` and
  `app/modules/proxy/_service/http_bridge/upstream_events.py`.
- Clients: a quota answer that previously surfaced as `stream_incomplete`/502
  now arrives with its real code and reset metadata.
- Tests: `tests/unit/test_proxy_utils.py`,
  `tests/unit/test_proxy_http_bridge.py`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: quota terminals survive a failed pre-created bridge
  replay, and post-refresh stream terminals preserve reset metadata.
