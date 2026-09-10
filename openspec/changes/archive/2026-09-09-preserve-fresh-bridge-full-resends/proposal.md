## Why

When an OpenCode HTTP Responses conversation loses its live bridge, codex-lb
currently injects the durable `previous_response_id` into a client-unanchored
full resend before opening a fresh upstream WebSocket. Upstream can leave that
anchored `response.create` completely eventless. Production telemetry in #1485
captured seven consecutive 240-second `missing_response_created_timeout`
failures in one hour for the same tool-heavy conversation, each with no
`response.created` or other upstream event.

The affected full resend carries complete direct tool call/output pairs after
the verified stored prefix, so it can continue the tool loop without the old
response anchor. Adding an anchor changes that request and can strand it on a
response id that the new WebSocket does not acknowledge. Ordinary cumulative
prompts that omit prior assistant output must remain anchored to avoid losing
conversation context.

## What Changes

- Preserve a client-unanchored full resend when its stored prefix matches the
  durable conversation, its projected suffix either retains completed
  assistant output before fresh user input or is a self-contained direct tool
  call/output loop that exactly settles the durable prior-response call
  manifest, and neither a reusable local bridge nor a forwardable remote owner
  exists.
- Persist the complete prior-response tool-call ID/type manifest atomically with
  each durable previous-response alias. A manifest is known only when every
  observed tool-call `output_item.added` reaches a matching `output_item.done`
  and terminal output does not reveal another call. The serialized manifest is
  bound to its response ID so an older rolling-upgrade writer cannot leave stale
  calls attached to a newer response. Existing or incomplete rows remain
  anchored.
- Open the fresh bridge on the durable owner with the original hard affinity,
  without adding `previous_response_id` or trimming the full resend.
- Avoid seeding the newly created session with the old durable response before
  its first request, which would otherwise re-inject the same anchor at the
  session-level optimization.
- Emit a structured bridge event when the original full resend is preserved.
- Add unit and public `/v1/responses` route regression coverage.
- Leave live-session trimming, client-supplied anchors, owner-unavailable
  recovery, and account-neutral replay rules unchanged.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: A verified client-unanchored full resend with complete
  retained response context remains unanchored for the first request on a fresh
  durable HTTP bridge.

## Impact

- Affected code: HTTP Responses bridge durable reattach preparation, durable
  bridge state persistence, and the bridge-session migration.
- Affected tests: durable session persistence, replay-safety and HTTP bridge
  unit coverage, migration checks, and the public `/v1/responses` route.
- Adds one nullable internal `http_bridge_sessions` text column. Existing rows
  require no backfill and fail closed to anchored behavior.
- No public endpoint, response schema, setting, dependency, or cross-account
  routing behavior changes.
