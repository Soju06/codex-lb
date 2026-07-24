## Why

Multiple production OpenCode sessions repeatedly became silent after codex-lb recreated their HTTP bridge on a fresh upstream WebSocket. The same failure reproduced during workload replacement and again after both replacement pods were ready: the client supplied a full conversation resend whose prefix matched durable context, but codex-lb replaced that context with the latest durable `previous_response_id`. The fresh upstream connection emitted no `response.created` or error for that old anchor, so each turn remained pending until either the client cancelled or the bridge watchdog expired.

## What Changes

- When no live bridge owner exists and a hard-continuity request contains a fingerprint-verified full resend, keep routing to the durable owner account but submit the complete request without injecting the old durable `previous_response_id`.
- Continue injecting the durable anchor for incremental requests that need it to preserve context.
- Do not replay timed-out or otherwise ambiguously accepted work.
- Add regression coverage for both the full-resend fresh path and the existing incremental reattach path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Prefer a verified client full resend over an ephemeral durable response anchor when creating a fresh upstream bridge.

## Impact

- Affected code: HTTP bridge durable reattach request preparation.
- Affected surface: streaming HTTP Responses requests with hard session continuity after their live upstream bridge is gone.
- No new account-movement path, schema change, setting, dependency, or transparent post-send replay.
