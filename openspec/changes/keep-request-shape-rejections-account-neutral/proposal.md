# Keep request-shape rejections account neutral

## Why

`_handle_stream_error` (`app/modules/proxy/_service/streaming/helpers.py`) is the
single call site of `LoadBalancer.record_error`. It skips the penalty only when
`_is_account_neutral_error_code(code)` matches, and that predicate covers local
overload, process-network, `proxy_unavailable`, and
`responses_compact_input_too_large`. An upstream `invalid_request_error` is
therefore charged to whichever account happened to serve the request — including
the missing-tool-output 400 that upstream raises when the *client's* input array
references a tool call with no matching tool output.

That 400 reproduces identically on every account, and a client that keeps
re-sending the same self-inconsistent conversation reproduces it on every
retry. The penalty then compounds through routing:

1. `app/core/balancer/logic.py` puts an account with `error_count >= 3` into
   `min(300, 30 * 2 ** (error_count - 3))` seconds of error backoff and moves it
   to `in_error_backoff` instead of `available`.
2. The backoff rescue only fires when `len(in_error_backoff) > 1` or a
   hard-blocked account also exists.
3. `app/modules/proxy/_load_balancer/sticky_selection.py` narrows the candidate
   set of a hard Codex-session sticky request to the single resolved owner
   account. That single-element pool can never satisfy the rescue condition, so
   selection returns `hard_affinity_saturated` and the request fails with an
   immediate 502 — no failover, no wait.

So one client's poisoned conversation degrades shared account health and 502s
unrelated, hard-pinned sessions on the same fleet. These 502s fail before an
account is assigned, so they are largely invisible in `request_logs`.

Field evidence over one 24h window on a shared deployment: ~25.7k upstream 400s
all attributable to a single repeated tool-call id from one client, matched 1:1
by `Recorded transient account error code=invalid_request_error` log lines, and
~2.2k 502s of which 72% were `hard_affinity_saturated` — 95% of those belonging
to a tenant that never sent the offending payload. One five-minute window served
zero successful requests.

## What Changes

- `_handle_stream_error` MUST NOT mutate account health for an upstream
  rejection of the request payload itself. A new narrow predicate,
  `_is_account_neutral_request_rejection`, gates the skip on
  `invalid_request_error` + HTTP 400 (or unknown status) + a classified
  missing-tool-output message, and logs the skip so the decision stays
  observable.
- Classification (`classify_upstream_failure`), the failover decision, and the
  client-visible error are unchanged: `non_retryable` still surfaces, and the
  existing continuity masking paths still rewrite the error where they already
  did.
- `_is_missing_tool_output_error` is split so its message test
  (`_is_missing_tool_output_message`) is reusable without a `param` value, since
  `UpstreamError` does not carry `param`.

## Design note: why not neutralize `invalid_request_error` wholesale

Some 400s carrying `invalid_request_error` are genuinely account scoped — the
model-entitlement rejection `The '<model>' model is not supported when using
Codex with a ChatGPT account.` (#876) is the in-repo example, already matched by
`_is_account_model_unsupported_error`. Gating on the code alone would stop a
genuinely unusable account from backing off. Gating on `param == "input"` was
also rejected: account-scoped hosted state can appear under `param=input`
(`file_id`, `item_reference`, and the other
`_ACCOUNT_SCOPED_HOSTED_INPUT_TYPES`), so `param=input` does not prove account
independence. The invariant this change asserts is narrower and provable from
the payload alone:

> An error that would reproduce identically on every account MUST NOT mutate one
> account's health.

## Non-goals

- Letting hard-sticky selection rescue a single-element `in_error_backoff`. A
  hard-pinned session has no alternative account by construction, so the rescue
  condition `len(in_error_backoff) > 1 or hard_blocked_exists` is unreachable
  for it; that is a separate routing-policy decision and is deliberately not
  bundled here.
- Changing the client-visible status or body for any rejection.

## Issue Trace

- Refs #1505, #876, #1168

## Impact

- **Spec**: `account-routing`
- **Behavior**: a malformed client payload no longer drives its serving accounts
  into error backoff, so hard-pinned sessions on unrelated accounts stop
  receiving `hard_affinity_saturated` 502s.
- **Persistence/UI**: no database, migration, configuration, or dashboard
  changes.
