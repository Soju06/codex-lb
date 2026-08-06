## 1. Regression Contract

- [x] 1.1 Add a native-WebSocket regression that sends a generated turn over a prewarm-opened connection and confirm it fails because the turn is logged as `prewarm`.
- [x] 1.2 Extend prewarm, request-log API, repository, and migration coverage for the separate `connection_request_kind` field.

## 2. Turn Attribution

- [x] 2.1 Retain the parsed direct-WebSocket handshake request kind separately and derive the initial per-turn kind from `generate: false`.
- [x] 2.2 Refine zero-output direct-WebSocket completions to prewarm before continuity, settlement, and logging decisions while keeping generated/error turns normal.

## 3. Persistence

- [x] 3.1 Add the nullable `request_logs.connection_request_kind` column from the current Alembic head without historical backfill.
- [x] 3.2 Carry connection request kind through request-log persistence and expose it from the request-logs API.

## 4. Verification

- [x] 4.1 Run targeted WebSocket, request-log, and migration tests plus formatting, lint, and type checks for changed files.
- [x] 4.2 Sync the responses compatibility requirement, validate and verify OpenSpec, archive the completed change, and run the repository local CI gate.
