## 1. Replica client identity

- [x] 1.1 Backport per-replica version warmup and fallback; verify follower fingerprint, lookup failure, cancellation, disabled scheduler, and override tests.

## 2. Trusted outbound headers

- [x] 2.1 Backport normalized subscription hints through HTTP, WebSocket, bridge reconnect, and compaction; verify transport, tier-policy, inbound isolation, and non-subscription regression tests.
- [x] 2.2 Backport HTTP hop-by-hop sanitation; verify native/non-native builders and compact route behavior with fixed and Connection-nominated headers.

## 3. Burst rejection recovery

- [x] 3.1 Backport runtime burst cooldown and owner-aware decisions; verify fresh/sticky selection, single-pool fallback, coded quota isolation, and bounded backoff tests.
- [x] 3.2 Adapt stream retries and startup probe ownership; verify consecutive waits, final HTTP 429/Retry-After, file/payload owners, post-refresh, cancellation, and reservation settlement ordering.

## 4. Integrated validation and handoff

- [x] 4.1 Run focused integrated backend suites, key-dashboard regressions, lint/type/architecture/timing gates, and strict OpenSpec validation; record commands, outcomes, and limitations.
- [x] 4.2 Sync canonical specs and stable context, verify all change requirements, and archive only after verification passes; confirm no schema/native/installer changes or production mutations.
