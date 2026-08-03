## 1. Contract and red tests

- [x] 1.1 Record current source, runtime, database, client, and route-probe baselines without credential values.
- [x] 1.2 Add strict OpenSpec deltas for OAuth identity, policy persistence/UI, and dual caller Live behavior.
- [x] 1.3 Add failing identity cache/singleflight, exact-seat, policy, migration, HTTP/three-WS auth matrix, nullable-log, and Key affinity compatibility regressions.

## 2. Shared verified identity

- [x] 2.1 Extract a typed Codex OAuth identity resolver while preserving the existing usage dependency contract.
- [x] 2.2 Add bounded token-plus-account caching, singleflight, token-expiry cap, short credential-denial caching, and test reset hooks.
- [x] 2.3 Add exact per-seat/workspace lookup with unique legacy fallback and credential-safe errors.

## 3. Policy persistence and dashboard

- [x] 3.1 Add policy ORM models, reversible Alembic migration, repositories/services/schemas, and dashboard APIs.
- [x] 3.2 Add Accounts-page policy editor, allowed-account multi-select, translations, API hooks, tests, and screenshots.

## 4. Unified realtime caller

- [x] 4.1 Add `RealtimeCallerScope` and one HTTP/WS resolver with strict Proxy Key classification and OAuth policy authorization.
- [x] 4.2 Make account selection and exact-owner reattach accept explicit allowed ids without changing ordinary proxy signatures.
- [x] 4.3 Preserve raw Key affinity input, add OAuth affinity input, support nullable API-key logging, and retain Key limits/assignments/last-used behavior.

## 5. Verification and delivery

- [x] 5.1 Run focused Python/frontend/migration/OpenSpec/docs/static suites and full local CI.
- [x] 5.2 Build wheel/frontend/checksums, back up runtime state, deploy through LaunchAgent with bounded health rollback, and verify automatic restart.
- [ ] 5.3 Configure both Codex route overrides and pass real local OAuth Live Voice, Key regression, owner continuity, policy revoke, logging/privacy, app restart, and rollback acceptance.
  - [x] Both route overrides, real app-server WebRTC network E2E, Key regression, owner continuity, policy revoke, logging/privacy, and automatic LaunchAgent restart.
  - [ ] Audible ChatGPT.app UI acceptance after app restart and an operator-triggered production rollback remain release gates.
- [ ] 5.4 Sync verified deltas to main specs/context, prepare atomic commits, and assemble upstream PR evidence on current main.
