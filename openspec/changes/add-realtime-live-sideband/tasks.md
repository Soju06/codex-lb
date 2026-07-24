## 1. Realtime call ownership

- [x] 1.1 Add final-success account observation to every Codex control-request success path without changing response semantics or account retry classification.
- [x] 1.2 Parse valid `rtc_...` call ids from successful realtime call `Location` headers and persist API-key-scoped hashed ownership with a fixed TTL.
- [x] 1.3 Add bounded opportunistic cleanup for expired reserved realtime-call affinity rows.

## 2. Frameless sideband transport

- [x] 2.1 Add a dedicated upstream live WebSocket URL/header builder that preserves Frameless query/session/alpha/attestation metadata, replaces credentials, and omits Responses-only beta headers.
- [x] 2.2 Add an authenticated `/v1/live/{call_id}` route that resolves the exact owner, enforces current API-key account scope, acquires a stream lease, and forbids failover or token refresh.
- [x] 2.3 Relay text, binary, close, and error frames bidirectionally with bounded frame sizes, deterministic cleanup, existing upstream-route policy, and no new payload logging.
- [x] 2.4 Record credential-safe route/request metadata without degrading global account health on capability-specific sideband failure.

## 3. Regression coverage

- [x] 3.1 Test call-id parsing, API-key-scoped hashing, raw-value non-persistence, TTL expiry, and opportunistic cleanup.
- [x] 3.2 Test final account capture across initial success, pre-visible failover, and forced-refresh success paths.
- [x] 3.3 Test authenticated sideband routing, exact-owner selection, assignment-scope enforcement, missing/stale binding denial, and no-account-failover behavior.
- [x] 3.4 Test upstream URL/header construction, credential replacement, attestation/alpha preservation, Responses-beta omission, direct/proxied egress, and handshake errors.
- [x] 3.5 Test text/binary relay, close/error propagation, disconnect cancellation, stream-lease release, and no token-refresh invocation.

## 4. Verification

- [x] 4.1 Run strict OpenSpec validation and all targeted Codex control, sticky repository, WebSocket connector, API route, and integration suites.
- [x] 4.2 Run Ruff check/format, ty, architecture/simplicity ratchets, and repository diff-hygiene checks.
- [ ] 4.3 Run a bounded credential-redacted protocol probe with exactly one preselected existing account token, no refresh endpoint or refresh manager call, and no access to any other pooled account.
- [ ] 4.4 Review the standalone diff against current upstream `main`, verify repository cleanliness expectations, and prepare an upstream pull request with the private/experimental scope stated explicitly.
