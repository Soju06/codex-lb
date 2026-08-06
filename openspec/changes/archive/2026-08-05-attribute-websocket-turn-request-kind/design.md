## Context

See `proposal.md` for the attribution failure. The downstream Codex metadata header is immutable for the lifetime of a WebSocket, while `response.create` payloads and terminal usage are per-turn. The current request state stores only the parsed header value and passes it to all terminal log paths.

Request-log usage data may already have been folded into hourly rollups. The repository contract explicitly disallows changing dimensions on folded rows because doing so leaves raw logs and rollups inconsistent.

## Goals / Non-Goals

**Goals:**

- Make `request_kind` a per-turn value on direct Responses WebSockets.
- Retain the connection-scoped handshake value as a distinct observable field.
- Keep prime settlement and continuity behavior aligned with the corrected turn classification across success and failure paths.

**Non-Goals:**

- Changing Codex CLI metadata or its startup prewarm behavior.
- Changing current-request account selection, traffic-class admission, quota enforcement, per-request budgeting, or API-key reservation settlement.
- Reclassifying historical rows or rebuilding historical usage rollups.

## Decisions

### Store connection and turn classifications separately

`_WebSocketRequestState.connection_request_kind` will retain the parsed handshake value for direct WebSocket requests. `request_kind` will become the effective per-turn classification and will be the only value used by terminal settlement and existing request-kind reporting.

The database and request-logs API will add nullable `connection_request_kind`. It is nullable so existing rows and non-WebSocket transports do not claim connection metadata that was never recorded. Direct WebSocket rows will persist the parsed value (`normal` or `prewarm`). This is not redundant state: the two fields describe different scopes.

Alternative considered: discard the handshake value after deriving the turn kind. That produces a smaller schema change but loses useful evidence about why a socket was opened and makes this class of attribution bug harder to diagnose.

### Classify at preparation, then refine from terminal usage

When the parsed metadata is `prewarm`, a frame with `generate: false` is classified as `prewarm` immediately. A generated frame is initially `normal`, which makes connect failures, timeouts, and other terminals without usage report the correct turn kind. On direct WebSocket completion, zero output tokens refine the turn to `prewarm` before continuity, health, settlement, and logging decisions run.

Alternative considered: classify only at completion. That cannot correctly attribute failure paths where usage is unavailable. Classifying only from `generate` also leaves the native completion discriminator unused even though it already defines empty-prewarm settlement behavior.

### Do not backfill historical request kinds

The additive migration only adds the nullable connection column. Historical `request_kind` rows remain untouched: changing a folded dimension would diverge usage rollups, and failed/cancelled rows do not retain the `response.create` payload needed to distinguish a failed prime from a generated turn. Correct attribution begins with newly written rows.

Alternative considered: rewrite successful `prewarm` rows with non-zero output. This is deterministic for those raw rows but unsafe once the same dimension has been folded into aggregate tables.

## Risks / Trade-offs

- [A rare generated turn completes with zero output on a prewarm-opened socket] -> It is classified as prewarm. The heuristic is deliberately limited to connections whose handshake declared prewarm and matches the existing native-WebSocket settlement discriminator.
- [Older rows remain misattributed until retention removes them] -> Keep the migration additive and document the forward-only attribution boundary rather than corrupting rollup parity.
- [Consumers do not know the new API field] -> The field is additive and nullable, preserving existing response consumers.

## Migration Plan

1. Add nullable `request_logs.connection_request_kind` from the current single Alembic head without rewriting rows.
2. Deploy code that writes the field for direct WebSocket requests and emits it in request-log API responses.
3. Roll back by deploying the previous code and downgrading the additive column; no existing values or request-kind rows need restoration.
