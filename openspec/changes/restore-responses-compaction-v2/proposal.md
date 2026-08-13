## Why

Current Codex clients can perform remote compaction through the Responses
stream contract by appending a final `compaction_trigger` input item. The
proxy's account-scoped upstream client still sends every compact request only
to the legacy `/responses/compact` JSON endpoint, which now returns upstream
404 for those clients.

## What Changes

- Attempt Responses-stream compaction first using the final
  `compaction_trigger` input item.
- Collect exactly one `response.output_item.done` compaction item and the
  terminal `response.completed` envelope into the proxy's existing compact
  response shape.
- Preserve the compaction item id, status, and encrypted content.
- Retain the legacy `/responses/compact` JSON request as a compatibility
  fallback when the stream endpoint is unavailable.
- Add regression coverage for stream success and legacy fallback.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require account-scoped compact requests to support
  the current Responses-stream compaction contract while retaining legacy
  endpoint compatibility.

## Impact

The change is limited to the upstream compact client, its focused tests, and
the Responses API compatibility specification. It adds no setting, database
migration, account-routing policy, or public endpoint.
