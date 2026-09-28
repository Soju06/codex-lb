# Change: record-resolved-upstream-transport-label

## Why

On the raw (non-bridge) streaming path, `_stream_with_retry` handles a
downstream-HTTP request that the policy keeps on upstream WebSocket with:

```python
upstream_stream_transport = "http" if policy_transport == "http" else configured_transport
```

This passes the configured strategy (`"auto"`) to the upstream client, which is
correct because `"auto"` keeps the WebSocket-handshake 426 fallback. It also
reuses the same variable for `request_logs.upstream_transport` and the
`codex_lb_upstream_transport_decisions_total` label. Every request that bypassed
the HTTP bridge (image, payload size, and so on) and still egressed over
upstream WebSocket is therefore logged as `upstream_transport = 'auto'`. The
value `'auto'` is a setting, not a transport.

An archived wire frame from a deployment shows `transport: "websocket"` for such
a request, while its log row reads `'auto'`. This hides the difference that
matters to an operator: the request reached upstream WebSocket without the
bridge session. #2425 reports the same `auto` bucket (342 of 404 image
requests).

## What Changes

- `request_logs.upstream_transport` and the transport-decision counter label
  record the resolved concrete transport, `"http"` or `"websocket"`.
- The mode handed to the upstream client is unchanged: a base `"auto"` stays
  `"auto"`, so the 426 fallback keeps working.
- No schema change, no new column, no new label value. Historical rows that
  hold `'auto'` stay as they are and remain readable.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: `Requirement: Request logs expose upstream Responses
  transport`. The scenario that pins `"auto"` is replaced in place.

## Superseded requirement text (for the PR body)

`openspec/specs/responses-api-compat/spec.md`,
`### Requirement: Request logs expose upstream Responses transport`, as of
`09a140fa9`:

> #### Scenario: downstream HTTP sticky records preserved auto upstream mode
> - **GIVEN** the downstream request transport is HTTP
> - **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request
> - **WHEN** the request log is persisted
> - **THEN** `transport` is `"http"`
> - **AND** `upstream_transport` is `"auto"`

## Out of scope

- The bridge bypass rule itself. That is the separate change
  `keep-replayed-history-images-on-http-bridge`, and it ships as a separate
  commit.
- Surfacing an in-client 426 fallback in the log. See `context.md`, "Known
  bound".
- Rewriting historical `'auto'` rows.

## Impact

- Dashboards and queries that count `upstream_transport = 'auto'` will see new
  rows split into `'websocket'` / `'http'`. The Request Logs API scenario that
  echoes a persisted `'auto'` value still holds for historical rows.
- `codex_lb_upstream_transport_decisions_total{upstream_transport="auto"}` stops
  growing. `"websocket"` grows by the same amount.
