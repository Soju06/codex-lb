## 1. Realtime call ownership

- [x] 1.1 Add final-success account observation to every Codex control-request success path without changing response semantics or account retry classification.
- [x] 1.2 Parse bounded `rtc_...` or UUID call ids only from documented realtime/live `Location` shapes and insert immutable API-key-scoped hashed ownership with a fixed TTL in a reserved namespace.
- [x] 1.3 Add throttled bounded-batch opportunistic cleanup for expired reserved realtime-call affinity rows.

## 2. Frameless sideband transport

- [x] 2.1 Add a dedicated upstream live WebSocket URL/header builder that preserves Frameless query/session/alpha/attestation metadata, replaces credentials and installation identity, and omits Responses-only beta headers.
- [x] 2.2 Add an API-key-required `/v1/live/{call_id}` route that resolves the exact owner under reattach policy, enforces current API-key account scope, acquires a stream lease, and forbids account failover, definitive-denial replay, or token refresh.
- [x] 2.3 Relay text, binary, and bounded close frames bidirectionally with bounded frame sizes, ping/pong liveness, deterministic cleanup, existing upstream-route policy, and no new payload logging.
- [x] 2.4 Record credential-safe route/request metadata, redact live ASGI path/query logging, and avoid degrading global account health on capability-specific sideband failure.

## 3. Regression coverage

- [x] 3.1 Test call-id and `Location` parsing, mandatory API-key-scoped hashing, immutable owner insertion, raw-value non-persistence, TTL expiry, and throttled bounded cleanup.
- [x] 3.2 Test final account capture across initial success, pre-visible failover, and forced-refresh success paths.
- [x] 3.3 Test API-key-required sideband routing even in auth-disabled mode, exact-owner selection, assignment-scope enforcement, missing/stale binding denial, no-account-failover behavior, and immediate post-refresh attachment against a nonzero stale selection cache using the freshly persisted token and identities.
- [x] 3.4 Test upstream URL/header construction, credential and installation replacement, attestation/alpha preservation, Responses-beta omission, direct/proxied egress, denial status preservation, and no definitive-denial replay.
- [x] 3.5 Test text/binary relay, bidirectional close-code/reason propagation, transport liveness, disconnect cancellation, stream-lease release, SDP trace suppression, path/query log redaction, and no token-refresh invocation.

## 4. Verification

- [x] 4.1 Run strict OpenSpec validation and all targeted Codex control, sticky repository, WebSocket connector, API route, and integration suites.
- [x] 4.2 Run Ruff check/format, ty, architecture/simplicity ratchets, and repository diff-hygiene checks.
- [ ] 4.3 Run a bounded credential-redacted protocol probe with exactly one preselected existing account token, no refresh endpoint or refresh manager call, and no access to any other pooled account.
- [x] 4.4 Review the standalone diff against current upstream `main`, verify repository cleanliness expectations, and prepare an upstream pull request with the private/experimental scope stated explicitly.
