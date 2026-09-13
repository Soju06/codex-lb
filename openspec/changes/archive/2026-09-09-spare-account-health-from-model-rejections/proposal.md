## Why

A model registered only on an OpenAI-compatible model source stops resolving to
that source the moment the source is disabled or removed, because source lookup
filters on `is_enabled`. The request then falls through to ordinary subscription
account selection, and every ChatGPT account rejects it with HTTP 400 and the
message `The '<model>' model is not supported when using Codex with a ChatGPT
account.`

Today each of those rejections records a transient account error. A single
client polling one unroutable model therefore drives *every* serving account
past `ERROR_BACKOFF_THRESHOLD` and pins it at the 300-second error-backoff
ceiling. On a live deployment one such client produced 4,800 rejections in six
hours across three healthy Pro accounts — one client request fans out to all
three accounts, so all three were penalized by every poll. Unrelated foreground
traffic on those accounts was then denied: sticky selection passes
`allow_backoff_fallback=False`, so a session hard-pinned to a poisoned account
failed with `continuity_owner_unavailable` / `No available accounts` while the
account itself was active with 1–7% quota used.

The rejection names the model, not the account. It says nothing about whether
the account can still serve the models it *is* entitled to, so it is not an
account-health signal. Excluding that account for the rest of the request —
which failover already does — is the correct and sufficient response.

## What Changes

- A model-entitlement rejection no longer records a transient account error,
  rate-limit penalty, quota penalty, or permanent failure.
- Failure classification, the failover decision, and the client-visible status
  and body are unchanged, so a differently entitled account is still tried.
- Membership is decided by the exact rejection message and a 400 status, not by
  the normalized error code. Upstream delivers this rejection over the Codex
  streaming path with neither `code` nor `type` set, which normalizes to the
  `upstream_error` fallback; the previous code-gated matcher never saw it there.
- The skip is logged, matching the existing account-neutral skip log.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: model-entitlement rejections are health neutral while
  remaining failover-eligible.

## Impact

- `app/modules/proxy/helpers.py` gains a code-agnostic matcher for the
  rejection message.
- `app/modules/proxy/_service/streaming/helpers.py` skips the account-health
  penalty for it in `_handle_stream_error`, the single funnel every transport
  uses for stream-error account health.
- No schema, config, or API surface changes.
