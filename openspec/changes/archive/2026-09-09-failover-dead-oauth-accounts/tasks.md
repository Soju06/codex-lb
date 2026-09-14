## 1. Account health

- [x] 1.1 Classify `token_revoked` as a permanent reauthentication failure.
- [x] 1.2 Preserve canonical HTTP 401 mapping for the upstream error spelling.
- [x] 1.3 Classify `token_revoked` through the shared WebSocket authentication-failure code set.

## 2. Scope and regression coverage

- [x] 2.1 Remove the overlapping compact requirement; #2080 owns forced-refresh failover.
- [x] 2.2 Cover revoked-token classification and its use with existing compact account ownership and settlement behavior.
- [x] 2.3 Make the HTTP bridge file-affinity ordering regression hermetic.

## 3. Validation

- [x] 3.1 Run focused unit and integration regressions.
- [x] 3.2 Run lint, type checks, and strict OpenSpec validation.

## 4. Images review follow-up

- [x] 4.1 Map terminal `token_revoked` Images errors to HTTP 401 independently of the upstream error type.
- [x] 4.2 Cover generation and edit HTTP envelopes on canonical and Codex-base routes for `response.failed` and `error` events without an authentication type.
- [x] 4.3 Verify the updated branch after merging current main and sync the requirements.

## 5. Proxy-route review follow-up

- [x] 5.1 Cover a Responses request whose forced refresh succeeds but whose same-account retry still returns `token_revoked`, including failover, persisted reauthentication status, and reservation settlement before health writes.
- [x] 5.2 Cover WebSocket `error` and `response.failed` revoked-token frames without an authentication type, including same-request replay ownership and accepted-response-ID no-replay guards.
- [x] 5.3 Run focused route regressions, lint, type checks, and strict OpenSpec validation.
