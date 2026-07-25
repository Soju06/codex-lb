## 1. Standalone contract

- [x] 1.1 Author one standalone `realtime-api-compat` delta with normative spec, evidence/rationale context, design, proposal, and this fresh unchecked task ledger; carry no `responses-api-compat` delta.
- [x] 1.2 Record `openspec status --change add-realtime-live-sideband` and `openspec instructions apply --change add-realtime-live-sideband` before product implementation.

## 2. Dashboard request-log contract

- [x] 2.1 Add and observe the failing full-row `RequestLogsResponseSchema` regression for `requestKind: "realtime_live"` with WebSocket transport.
- [x] 2.2 Add the closed-enum value, observe the targeted frontend test pass, and retain the existing rendering fallback without component changes.

## 3. Required call creation and final owner

- [x] 3.1 Add and observe public HTTP regressions for required keys, final owner after initial/failover/refresh success, immutable binding, and missing/unsupported `Location` fail-closed behavior.
- [x] 3.2 Implement dedicated required-key routing, final-success observation, bounded call-id parsing, and one non-replayed `503 realtime_call_binding_failed`; observe the focused HTTP suite pass.

## 4. Returned Location to every sideband

- [x] 4.1 Add and observe product-route regressions from returned `Location` through current-app, v3, and legacy ingress for both `rtc_...` and canonical UUID ids.
- [x] 4.2 Implement thin typed adapters, one normalizer, and exact v3/legacy upstream URLs with a single ordered legacy `call_id`; observe the route regressions pass.

## 5. Exact owner, key, lease, and persisted identity

- [x] 5.1 Add and observe regressions for cross-key denial, reassignment/unavailable/capped owners, current persisted credentials, no refresh/fallback, and exactly-once stream-lease release.
- [x] 5.2 Implement exact-owner resolution, assignment enforcement, reattach leasing, fresh owner loading, and fail-closed policy; observe focused service/integration suites pass.

## 6. Reserved persistence and operator cleanup

- [x] 6.1 Add and observe repository/API regressions for digest-only immutable ownership, TTL, bounded reserved-prefix cleanup, unrelated-row preservation, invisibility, and protection from single/bulk/filtered/delete-all operations.
- [x] 6.2 Implement reserved persistence, bounded cleanup, list exclusion, and delete protection; observe repository and dashboard API suites pass.

## 7. Transport, close, and error isolation

- [x] 7.1 Add and observe connector/relay regressions for protocol headers/query order, definitive-denial no-replay, cancelled-handshake cleanup, bounded peer close, paired-task cancellation, and live-vs-Responses `InvalidProxy` behavior.
- [x] 7.2 Implement the typed live connector and deterministic relay ownership while preserving ordinary Responses behavior; adapt the current-main fake close contract and observe focused unit suites pass.

## 8. Privacy and request-log observability

- [x] 8.1 Add and observe public-seam regressions proving SDP/frame bodies are absent, live path/query data is redacted, no archive runs, and the producer emits `realtime_live`/`websocket` rows.
- [x] 8.2 Implement trace suppression and credential-safe request logging; observe focused privacy and request-log suites pass.

## 9. Zero-config and final focused verification

- [x] 9.1 Prove the private feature requires an existing registered key while base proxy/dashboard startup needs no new setting or setup; verify no setting, migration, dependency, model, nav, README, `.env.example`, or docs path changed.
- [x] 9.2 Run all affected Voice and existing Responses regressions, dashboard schema tests, current-main upstream-path test, targeted frontend lint/type/test, Ruff check/format, ty, LSP diagnostics, architecture/simplicity ratchets, and `git diff --check`.
- [x] 9.3 Run `openspec validate add-realtime-live-sideband --strict`, `openspec validate --specs --strict`, and `/opsx:verify`-style completeness/correctness/coherence review; record final status.
- [x] 9.4 Verify the exact 27-path allowlist (`18 M`, `9 A`, `0 D`), oracle-byte equivalence or documented divergence, no symlink/media/private-path content, canonical WORKTREE digest, attribution plan, and uncommitted `FOCUSED_GREEN` stop.
