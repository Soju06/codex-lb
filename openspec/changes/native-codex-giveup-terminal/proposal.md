## Why

The native Codex HTTP Responses path can commit a 200 response, forward a
substantial upstream prefix, and then exhaust codex-lb's transport retries.
The retry layer currently suppresses its synthetic transport terminal for native
clients. The public normalizer then sees only EOF and raises
`ProxyResponseError`, so the client receives no `response.failed` event and
reconnects blindly.

The concrete incident was request
`b81d9e10-59bd-47fb-b642-5f99dfe459d8`: 18 chunks / 111,687 bytes were
delivered before `responses_stream_terminal_delivery` recorded
`exception_before_terminal` at the normalizer's missing-terminal raise.

## What Changes

- Preserve the internal synthetic transport marker when a native stream has
  already become downstream-visible, and also when account attempts are
  exhausted before the first visible frame.
- At the native HTTP Responses boundary, translate that marker (or an equivalent
  exhausted transport exception) into exactly one `response.failed` event with
  `error.code = "rate_limit_exceeded"` and a deterministic `Please try again in
  <N>s.` hint. Put the canonical hint first so an upstream message cannot
  override the delay parser.
- Keep the original upstream code in account-health and request-log paths. The
  retryable code is an egress-only native-client translation; it is not a quota
  observation and does not change account selection.
- Keep an unmarked raw EOF on the existing native abort path, preserve local
  pre-dispatch refusals, and leave non-native Responses shaping unchanged.
- Close the suspended stream-generator chain after a marked terminal so the
  account lease and upstream transport are released instead of accumulating
  until restart.

## Impact

- Affected capability: `responses-api-compat`.
- Affected code: native HTTP Responses retry/normalization and shared error
  message construction.
- No new setting, migration, dependency, WebSocket relabel, or deployment
  topology change.
- Regression coverage exercises the observed committed-stream shape, the
  no-visible-attempt exhaustion shape, competing upstream retry hints, local
  refusals, and non-native behavior.
