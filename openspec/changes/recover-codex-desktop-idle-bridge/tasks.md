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
