## 1. Eventless pre-created deadline

- [x] 1.1 Record the current monotonic `response.create` send timestamp in HTTP bridge request state and replace it on every real send.
- [x] 1.2 Add a pure client-safe deadline helper that uses the smaller of the existing stuck-gate threshold and 240 seconds.
- [x] 1.3 Enforce the deadline from the upstream reader without requiring a second gate waiter or SSE keepalives; recheck narrow eventless eligibility before acting.
- [x] 1.4 Fail and retire the whole bridge session through existing settlement, logging, and Prometheus paths without replay, account movement, or account-health writes.
- [x] 1.5 Add focused regressions for no-waiter expiry, send-time anchoring, leading telemetry, created/eventful/downstream protection, terminal settlement, and account neutrality.

## 2. Native Codex SSE liveness

- [x] 2.1 Separate native heartbeat identity from payload-shape normalization while preserving explicit SDK-marker precedence and public `/v1` behavior.
- [x] 2.2 Add endpoint-level regressions proving Desktop receives `codex.keepalive` data frames while explicit SDK and public clients retain comment/vendor-safe streams.

## 3. Verification

- [x] 3.1 Run focused bridge and API tests, then Ruff, formatting, type, and architecture checks.
- [x] 3.2 Validate the change strictly and validate all repository specs.
- [x] 3.3 Review the final diff for secrets/header leakage, account-affinity changes, replay, missing settlement, metric loss, and unrelated edits.

## 4. Durable anchor quarantine

- [x] 4.1 Add repository/coordinator regressions for clearing the expected durable latest-response anchor and all coupled metadata while preserving a newer response id or fenced owner.
- [x] 4.2 Add bridge regressions proving the eventless watchdog quarantines only a proxy-injected anchor and a subsequent full-context request does not receive the quarantined id.
- [x] 4.3 Implement the fenced conditional durable-anchor clear and invoke it before missing-`response.created` retirement releases durable ownership.

## 5. Verification of the quarantine extension

- [x] 5.1 Run focused durable coordinator, HTTP bridge unit, and HTTP bridge integration tests, then Ruff, formatting, type, and architecture checks.
- [x] 5.2 Validate the change strictly and validate all repository specs.
- [x] 5.3 Review the final diff for stale-write safety, accidental alias deletion, anchorless automatic replay, account-affinity changes, account-health writes, settlement loss, and unrelated edits.

## 6. Review hardening

- [x] 6.1 Carry proxy-injected anchor provenance through the owner-forward context and API-to-streaming path.
- [x] 6.2 Bind provenance to the structured HMAC contract, prevent legacy downgrade, and add add/change/strip tamper regressions.
- [x] 6.3 Preserve durable input count and fingerprint during quarantine while clearing the exact response anchor and pending-tool metadata.
- [x] 6.4 Derive quarantine from existing durable fields, allow only a verified safe full-context resend without an anchor, and fail incremental or mismatched requests closed before transport creation.
- [x] 6.5 Add cross-replica propagation, safe full-resend, incremental fail-closed, and fenced persistence regressions.

## 7. Final verification

- [x] 7.1 Sync the updated normative requirements and context back to the main capability specs.
- [x] 7.2 Run focused bridge, forwarding, API-contract, and durable coordinator tests, then Ruff, formatting, type, and architecture checks.

## 8. Mid-tool quarantine recovery hardening

- [x] 8.1 Specify the fingerprint-matched, self-contained complete tool-call/output recovery path and keep generic/cross-account replay unchanged.
- [x] 8.2 Add pure replay-safety regressions for complete mid-tool suffixes, immediate retries without a new user message, orphan outputs, unresolved calls, duplicate ids, and unsupported state.
- [x] 8.3 Apply the alternative only to the quarantined-anchor guard and add `/backend-api/codex/responses` bridge regressions proving allow and fail-closed outcomes before transport creation.

## 9. Final verification

- [ ] 9.1 Validate the OpenSpec change and all repository specs strictly, then complete the Codex review loop.

## 10. Disconnect invalidation hardening

- [x] 10.1 Specify the production two-stage disconnect sequence, official connection-local `store=false` recovery contract, and the boundary between proactive anchor invalidation and unsafe transparent replay.
- [x] 10.2 Keep quarantine fail-closed when usable prefix proof is absent, and preserve proxy-injected provenance across same-anchor owner-forward local rebinds.
- [x] 10.3 Record effective request `store` provenance and proactively quarantine only an actually sent, unambiguous proxy-injected `store=false` anchor at upstream disconnect before existing safe no-anchor replay mutates request provenance.
- [x] 10.4 Add repository, owner-forward, selector, and upstream-reader regressions covering sentinel replacement, queued/store/client/newer-owner protection, disconnect close variants, and no new replay/account movement/health writes.

## 11. Final verification after disconnect hardening

- [x] 11.1 Sync the normative requirements and operational context to the main specs.
- [ ] 11.2 Run focused and full required checks, strict OpenSpec validation, and the Codex review loop; resolve all Critical/High findings before PR preparation.

## 12. Idle-close and restart lineage hardening

- [x] 12.1 Specify current-socket latest-response provenance, idle disconnect quarantine, and the fresh-socket `store=false` lineage boundary.
- [x] 12.2 Record and reset current-socket completion provenance, extend disconnect selection to an idle latest response, and prevent automatic durable-id injection on a fresh socket.
- [x] 12.3 Add idle-close, reconnect/reset, fresh-socket full-history, mid-tool, incremental, and explicit-client-anchor regressions.
- [x] 12.4 Sync the final normative requirements and stable operational context to the main specs.
- [x] 12.5 Revalidate proxy-injected socket provenance after response-create admission, clear provenance when a different durable id replaces local state, and add fail-closed/full-history regressions.

## 13. Correlated WebSocket egress outage hardening

- [x] 13.1 Specify bounded same-egress, cross-account no-close correlation and preserve single-account, different-egress, explicit-close, no-replay, and account-affinity behavior.
- [x] 13.2 Add detector, adapter, HTTP bridge, and direct WebSocket regressions proving all correlated candidates remain account neutral while negative controls retain health penalties.
- [x] 13.3 Implement bounded process-local correlation before Responses receive failures reach account-health settlement.
- [x] 13.4 Sync the normative behavior and operational context to the owning main specs.
- [ ] 13.5 Run focused and full required checks, strict OpenSpec validation, final review, and deployment-safety audit.

## 14. Actionable full-resend-required errors

- [x] 14.1 Specify and sync the non-retryable HTTP 400 full-resend-required contract for quarantined anchors, fresh-socket automatic anchors, and final send-boundary lineage loss.
- [x] 14.2 Add one dedicated `continuity_requires_full_resend` helper and use it only in the deterministic local guards.
- [x] 14.3 Add unit and backend-route regressions for the stable envelope, repeated Goal-style incremental rejection, no transport creation, and no account-health side effects.
- [ ] 14.4 Run focused and full required checks, strict OpenSpec validation, final review, and deployment-safety audit.
