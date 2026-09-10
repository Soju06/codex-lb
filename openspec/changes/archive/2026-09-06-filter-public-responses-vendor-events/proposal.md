# Filter public Responses vendor events

## Why

The public Responses stream currently drops `codex.*` events but forwards
`responsesapi.websocket_timing`. Issue #1934 reports that IntelliJ's strict
event deserializer aborts on this upstream diagnostic before receiving
`response.completed`.

## What Changes

- Apply the existing public event-family contract to all string event types:
  allow `response.*` and `error`, and drop other types.
- Preserve vendor events when native Codex contract enforcement is disabled.
- Cover public and native HTTP/SSE routing with regression tests.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: clarify filtering beyond the `codex.*` namespace
  and distinguish native requests from OpenAI-shaped backend requests.

## Impact

- Code: `app/modules/proxy/api.py`.
- Tests: Responses stream contract unit tests and Responses route integration tests.
- No new configuration, dependencies, database changes, or dashboard changes.
- Partial coverage of #1934; `response.instructions` compatibility is out of scope.
