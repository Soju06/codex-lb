## Why

Request logs currently retain client metadata but do not retain the conversation
identifier exposed by supported coding harnesses. Operators therefore cannot
group related requests, inspect a conversation's request count and cost, or
measure distinct conversations in dashboard and report statistics.

## What Changes

- Detect Codex conversation IDs from `thread-id` and OpenCode IDs from the
  ordered `x-parent-session-id`, `x-opencode-session`, `x-session-id`, and
  `x-session-affinity` headers, without changing proxy routing or forwarded
  headers.
- Persist the detected ID as a nullable, indexed request-log field through all
  HTTP and WebSocket request-log paths.
- Add conversation filtering and pagination-independent request-count and cost
  aggregation to the request-log API, including the filter in the listing-count
  cache signature.
- Add conversation filtering controls and summaries to the dashboard, plus
  distinct conversation metrics and report table/export data.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `proxy-runtime-observability`
- `query-caching`
- `frontend-architecture`

`responses-api-compat` is unaffected because this change does not alter routing
or wire compatibility.

## Impact

- **Code:** request-log metadata, persistence, API filtering/aggregation,
  dashboard metrics and filtering, and report aggregation/rendering.
- **Schema:** one additive nullable `request_logs.conversation_id` column and
  an `idx_logs_conversation_id` index; existing rows remain null.
- **Compatibility:** detection is observational; unsupported or missing
  conversation headers continue normally and store null.
- **Operations:** no new dependency, feature flag, backfill, or setting.
