## Why

Production diagnostics captured an HTTP bridge upstream WebSocket ending with
no close frame after nine valid response events and visible model output. The
request correctly failed as `stream_incomplete`, but codex-lb also charged the
transport loss to the account. Three such infrastructure-level losses can put
the response owner into transient error backoff and turn a later pinned
continuation into `previous_response_owner_unavailable`.

Application progress answers whether replay is safe; it does not prove that a
frame-less socket loss is an account fault. Valid events before the loss are
positive evidence that authentication, model access, admission, and the
application protocol were working.

## What Changes

- Treat terminal HTTP bridge WebSocket endings with no upstream-authored close
  frame (`None` or synthetic RFC 6455 code `1006`) as account-neutral regardless
  of response-event or buffered-output progress.
- Keep the existing `stream_incomplete` request failure, socket retirement,
  settlement, operation acknowledgement, and no-replay behavior after output.
- Feed only genuinely eventless, output-free drops into the existing windowed
  account drain signal; post-output drops do not contribute to that signal.
- Preserve existing penalties for non-clean upstream-authored close frames,
  protocol violations, authentication, quota, policy, and application-layer
  errors, while retaining the established clean pre-response `1000` exemption.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: account-health attribution for frame-less HTTP bridge
  transport endings no longer depends on request progress.

## Impact

The change covers HTTP bridge account-health accounting, the internal
`UpstreamWebSocketMessage.transport_ended` contract, and WebSockets,
aiohttp/Codex, and native-egress adapter mappings. It introduces no external
API, setting, schema, migration, retry, or routing change. It supersedes only
the observed-output penalty clause in `keep-abrupt-eventless-drop-account-neutral`.
