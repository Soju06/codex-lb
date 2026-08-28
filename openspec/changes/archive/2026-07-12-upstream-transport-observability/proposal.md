# Proposal — Upstream Transport Observability

## Why

PR #1093 made downstream-HTTP `/v1/responses` choose upstream HTTP for single-shot requests and preserve upstream WebSocket/auto for sticky requests. During live verification, the existing `request_logs.transport` field proved insufficient: it records the downstream client transport, so HTTP/SSE callers always show `transport = "http"` even when the request egressed upstream through WebSocket/auto.

The only existing upstream transport evidence is an INFO log line, but that line is not reliably visible under the default `fastapi run` log configuration. Operators therefore cannot answer the operational question the routing change introduced: did this request actually use upstream HTTP, auto/WebSocket, or native WebSocket?

## What changes

- Persist a new nullable `request_logs.upstream_transport` value for proxy request logs.
- Expose `upstream_transport` through the Request Logs API and frontend schemas so dashboards and API consumers can distinguish downstream transport from upstream egress transport.
- Emit a low-cardinality Prometheus counter for upstream transport decisions, labeled by downstream transport, upstream transport, policy, sticky, and status.
- Preserve existing `transport` semantics as downstream/client transport for backward compatibility.

## Non-goals

- Do not rename or repurpose `request_logs.transport`.
- Do not add high-cardinality labels such as request id, account id, API key id, or model to the new metric.
- Do not change transport routing decisions; this is observability only.

## Risks

- Adding a nullable request-log column requires migration coverage on SQLite/PostgreSQL.
- Metrics labels must stay low-cardinality to avoid Prometheus blowups.
- Existing frontend consumers must tolerate historical rows with `upstream_transport = null`.
