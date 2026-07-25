## 1. Standalone contract

- [x] 1.1 Author one standalone `realtime-api-compat` delta with normative spec, evidence/rationale context, design, proposal, and this fresh unchecked task ledger; carry no `responses-api-compat` delta.
- [x] 1.2 Record `openspec status --change add-realtime-live-sideband` and `openspec instructions apply --change add-realtime-live-sideband` before product implementation.

## 2. Dashboard request-log contract

- [x] 2.1 Add and observe the failing full-row `RequestLogsResponseSchema` regression for `requestKind: "realtime_live"` with WebSocket transport.
- [x] 2.2 Add the closed-enum value, observe the targeted frontend test pass, and retain the existing rendering fallback without component changes.

## 3. Required call creation and final owner

- [x] 3.1 Add and observe public HTTP regressions for required keys, final owner after initial/failover/refresh success, immutable binding, and missing/unsupported `Location` fail-closed behavior.
- [x] 3.2 Implement dedicated required-key routing, final-success observation, bounded call-id parsing, and one non-replayed `503 realtime_call_binding_failed`; observe the focused HTTP suite pass.
- [x] 3.3 Document the authorized endpoint family: implement only private installed-app `POST /backend-api/codex/realtime/calls`; treat SDK-documented `POST /v1/realtime/calls` and `POST /v1/realtime/client_secrets` as related context, not public routes.

## 4. Returned Location to every sideband

- [x] 4.1 Add and observe product-route regressions from returned `Location` through current-app, v3, and legacy ingress for both `rtc_...` and canonical UUID ids.
- [x] 4.2 Implement thin typed adapters, one normalizer, and exact v3/legacy upstream URLs with a single ordered legacy `call_id`; observe the route regressions pass.

## 5. Exact owner, key, lease, and persisted identity

- [x] 5.1 Add and observe regressions for cross-key denial, reassignment/unavailable/capped owners, current persisted credentials, no refresh/fallback, and exactly-once stream-lease release.
- [x] 5.2 Implement exact-owner resolution, assignment enforcement, reattach leasing, fresh owner loading, and fail-closed policy; observe focused service/integration suites pass.
- [x] 5.3 Cover every non-active persisted owner status and fail closed before token decryption or upstream attachment.

## 6. Reserved persistence and operator cleanup

- [x] 6.1 Add and observe repository/API regressions for digest-only immutable ownership, TTL, bounded reserved-prefix cleanup, unrelated-row preservation, invisibility, and protection from single/bulk/filtered/delete-all operations.
- [x] 6.2 Implement reserved persistence, bounded cleanup, list exclusion, and delete protection; observe repository and dashboard API suites pass.

## 7. Transport, close, and error isolation

- [x] 7.1 Add and observe connector/relay regressions for protocol headers/query order, definitive-denial no-replay, cancelled-handshake cleanup, bounded peer close, paired-task cancellation, and live-vs-Responses `InvalidProxy` behavior.
- [x] 7.2 Implement the typed live connector and deterministic relay ownership while preserving ordinary Responses behavior; adapt the current-main fake close contract and observe focused unit suites pass.
- [x] 7.3 Disable both definitive-handshake and routed-network replay for live sideband while proving ordinary Responses keeps default routed network fallback.

## 8. Privacy and request-log observability

- [x] 8.1 Add and observe public-seam regressions proving trace/archive sinks retain no SDP or frame bodies, live path/query data is redacted, and the producer emits `realtime_live`/`websocket` rows.
- [x] 8.2 Implement trace suppression and credential-safe request logging; observe focused privacy and request-log suites pass.
- [x] 8.3 Exercise redaction at the Uvicorn accepted-handshake log sink, reject unsupported duplicated-prefix Live aliases before acceptance, and pass a typed account-safe privacy policy through the realtime call adapter so every covered failure branch suppresses identifiers and exception details.

## 9. Zero-config and final focused verification

- [x] 9.1 Prove the private feature requires an existing registered key while base proxy/dashboard startup needs no new setting or setup; verify no setting, migration, dependency, model, dashboard navigation, README, or `.env.example` path changed.
- [x] 9.2 Run all affected Voice and existing Responses regressions, dashboard schema tests, current-main upstream-path test, targeted frontend lint/type/test, Ruff check/format, ty, LSP diagnostics, architecture/simplicity ratchets, and `git diff --check`.
- [x] 9.3 Run `openspec validate add-realtime-live-sideband --strict`, `openspec validate --specs --strict`, and `/opsx:verify`-style completeness/correctness/coherence review; record final status.
- [x] 9.4 Verify the changed-source allowlist plus absence of symlink, media, and private-path content using only project-verifiable evidence.
- [x] 9.5 Sync `realtime-api-compat` into the main spec and narrative context while retaining this active change; publish `docs/live-voice.md` with a main-spec backlink and register it in the MkDocs navigation.
- [x] 9.6 Run focused and complete Python/frontend/OpenSpec/docs validation after implementation hardening; record the project-verifiable results.

## 10. Credential-safe completion

- [x] 10.1 Add red-to-green regressions and fixed credential-safe branches for realtime call summary traces, unexpected/decryption failures, and direct Live handshake denials without changing ordinary control or Responses behavior.
- [x] 10.2 Exercise `realtime_live`/`websocket` persistence through `/api/request-logs`, replace hidden connector lookup with an explicit service seam, and cleanly rename shared `*ResponsesWebSocket*` transport types to generic `*UpstreamWebSocket*` names without aliases.
- [x] 10.3 Re-run focused and complete Python/frontend/OpenSpec/docs/static/runtime verification and record the project-verifiable results.

## 11. Post-verification hardening

- [x] 11.1 Add a red-to-green OpenTelemetry regression and redact private routed Live path/query data from exported aiohttp span URLs without changing the network request URL.
- [x] 11.2 Add red-to-green regressions and preserve the exact downstream WebSocket subprotocol offers plus the upstream-selected offered value across routed and direct Live transports, without changing Responses.
- [x] 11.3 Add red-to-green public request-log regressions and classify missing-Location or durable-binding failures as errors before the single call-create row is persisted, without replaying the created call.
- [x] 11.4 Exercise the actual `yarl.URL` aiohttp filter input and the rejected duplicated Live alias at the server-span sink; keep both credential-safe without changing ordinary traffic.
- [x] 11.5 Add red-to-green regressions for private process-network diagnostics, raw upstream subprotocol rejection, pre-stack OTEL instrumentation, rejected private path/query redaction, and Trusted Proxy scope projection while preserving ordinary call shapes and routing.
- [x] 11.6 Add red-to-green product-path coverage and fixes for AuthManager privacy-policy propagation and cancellation-resistant bounded upstream close cleanup.
- [x] 11.7 Re-run focused and complete Python/frontend/OpenSpec/docs/static/package verification plus final standards/spec/privacy review.
