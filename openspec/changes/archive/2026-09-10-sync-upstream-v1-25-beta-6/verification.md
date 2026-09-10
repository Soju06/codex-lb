# Verification: complete beta.6 integration

## Source and preservation

- Starting fork: `7ecb38deb2157aab7f7a13cb22bf8b23be16fb2c`.
- Frozen release / pending `MERGE_HEAD`: `e4c0164daae7e3ee3983d4455dee174441f76355` (`v1.25.0-beta.6`). This is a real `--no-commit --no-ff` merge, not selective cherry-picks.
- Pre-merge verified backports remain recoverable in stash `5fa1961cb6bc1dc925169df20a29023c203b0755`; the stash was applied, not dropped. Duplicate release fixes were reconciled with beta.6, including removal of a duplicate model-version warmup definition.
- The deployed usage-group migration remains byte-identical: SHA-256 `7fea00de666a2673d8b67e811b01ea473e0933209b2a5306260015bf6e2b6ce5`. Its parent remains `20260830_000000_add_quota_warmup_claim_expiry`.
- Unrelated `simplify-install-one-liners/.openspec.yaml` remains untracked and unchanged: SHA-256 `0d815ffdda0fec4cf9c7b2346a209fdec1bcde4f92cfb10c2075e2e381fd16f8`.
- No user-facing commit, push, production deployment, production database change, or CHANGELOG edit is included.
- Every pre-merge tracked backport test function remains present. The two untracked routing-hint test files already shipped in beta.6; their release versions are retained (the HTTP fixture uses the upstream model-source lease seam).
- Release-owned removals and archive moves, including obsolete vendored skill files and upstream OpenSpec changes, are imported as part of the full merge and remain recoverable from the parent commits.

## Requirement and design coverage

| Dimension | Evidence |
| --- | --- |
| Schema preservation | `20260910_010000_merge_beta6_and_key_groups.py` joins both parents without DDL. `test_beta6_merge_migration.py` covers fresh, fork-head, and upstream-head upgrades, retained key identity/group/limits/indexes, single head, merge-only downgrade, and re-upgrade. The same verifier passed against three separate PostgreSQL 18 databases. |
| Native safeguards | `native_egress.py` combines upstream bounded HTTP queues and interpretation with fork shared WebSocket byte budgets. Decoded payload allocations are charged. `test_native_websocket_capacity.py` covers 100/300/500 concurrent burst connections, interpreted bursts over 64 events, per-connection/shared overflow, ordered prefix/terminal delivery, and cleanup. All native tests from both parents remain present. |
| Ownership and settlement | Restored hop-by-hop sanitation and keyed terminal-burst settlement ordering. Proxy route/unit suites cover real API-key reservation settlement before health writes, file ownership, cancellation, 429 envelopes, trailing-slash routes, routing hints, and compact sanitation. |
| Fork compatibility | UTC+7 reset alignment and key-dashboard groups remain. Native diagnostics and reauthentication quarantine remain; the conflicting stock-upstream routability requirement was reconciled to the existing fork policy. |
| Configuration | Upstream dashboard settings and new tier map are retained. Existing fork native memory setting is T1; budget is 130 upstream fields plus that existing fork field, not a new setting. Settings reference regenerated. |
| Documentation | Both integration deltas are synchronized into canonical specs. Context explains schema and queue decisions. An upstream placeholder Purpose was completed, duplicate capacity requirement removed while retaining fork hard-owner protections, and imported trailing EOF whitespace normalized. |

## Completed checks

Artifacts are under `/tmp/codex-lb-beta6-merge.Csfswf`. Tests use isolated synthetic databases, an explicit nonexistent env file, and synthetic bootstrap credentials. The native wire tests use only the newly built helper.

| Check | Result / artifact |
| --- | --- |
| Python dependencies | `uv sync --dev --frozen` passed; CPython 3.14.7, package 1.25.0b6. |
| Frontend dependencies/build | Frozen Bun install and build/typecheck passed; `frontend-build.log`. Local Bun is 1.3.14; the upstream production Dockerfile pins Bun 1.4.2. |
| Frontend tests | 159 files / 1,296 tests passed; `frontend-test.log`. |
| Frontend lint | Passed; `frontend-lint.log`. |
| Real browser smoke | 5 passed against an isolated real backend; `browser-smoke.log`. |
| Rust | Pinned Rust 1.96.0, locked format/clippy/tests/release build passed in `codex-lb-beta6-native-check:local`; 26 non-doc tests passed; `rust-check.log`. |
| Native adapter/reset/settings | 78 passed; `native-reset-settings.xml`. |
| Real native wire and shared fixtures | 399 passed; `native-wire.xml`. |
| Proxy routes | 109 passed; `proxy-routes.xml`. |
| Broad proxy units | 2,541 passed; `proxy-unit-final.xml`. |
| Routing/quarantine/failover | 413 passed; `routing-preservation.xml`. |
| Selected HTTP bridge/WebSocket integration | 30 passed, 294 deselected; `bridge-websocket.xml`. |
| Settings/report integration | 111 passed; `settings-reports.xml`. |
| PostgreSQL fresh startup, key dashboard and report rollups | 25 passed; `postgres-suite.xml`. |
| PostgreSQL migration policy/remap and API-key routes | 108 passed on the disk-backed rerun; `postgres-policy-keys-final.xml`. |
| SQLite migration, key APIs/dashboard, limits and native capacity | 193 passed, 7 PostgreSQL-only skips; `migration-keys-verified.xml`. |
| Transport/fingerprint/timeout compatibility | 48 passed; `transport-compat.xml`. |
| Dashboard overrides / Codex client | 63 passed; `dashboard-codex-unit.xml`. |
| Three PostgreSQL merge paths | Fresh/fork/upstream upgrade, retained data/indexes, downgrade/re-upgrade all passed; `postgres-merge.log`. |
| Static checks | Ruff, format, ty, architecture, cancellation safety, timing seams and configuration tiers passed; corresponding `*-final.log`, `architecture.log`, `cancellation.log`, `timing.log`. |
| OpenSpec | All 65 canonical capabilities pass strict validation, and this change passes strict validation. |

The final listed backend/native invocations total 4,118 passes and 7 skips;
this is a sum of test executions, not a claim of distinct tests or full-suite
coverage. The full frontend run adds 1,296 passes, browser smoke adds 5, and
locked Rust tests add 26.

## Final assessment

Completeness: 9/9 tasks completed; both integration requirements and all four
delta scenarios have implementation and passing regression evidence. Correctness:
schema/data preservation and transport safeguards are covered on their actual
product paths. Coherence: the pinned full release, immutable deployed migration,
fork safety policies and byte budgets, restored fixes, and separate deployment
authorization boundary follow the design. No critical or unresolved integration
finding remains within the verified scope; the broader CI limitations below
remain explicit. Final consistency checks passed and the change was archived
on 2026-09-10 with both deltas synchronized.

## Failed attempts and limitations

- Initial native regression commands named a nonexistent test file and ran no tests; corrected paths are represented by the passing reports above.
- A newly added interpreted-burst fixture initially used the wrong IPC event name; it now uses the actual `websocket_responses_text` protocol. A test payload also needed an explicit `JsonValue` annotation for type checking.
- Restoring the model-fetcher test initially targeted the wrong lease seam. The actual `lease_http_session` seam is retained and the entire 2,541-test proxy set was rerun successfully.
- New migration tests initially used ambiguous `downgrade -1` at a merge node; explicit parent targeting verifies the merge-only downgrade instead.
- Historical migration fixtures built current models while claiming old revision stamps, leading to duplicate `usage_group` DDL. Tests now construct the pre-group schema before simulating those old stamps. The deployed migration was not made idempotent or rewritten. The final complete SQLite migration/key run passed after these fixture corrections.
- The first supplemental PostgreSQL policy/key run passed 20 tests before its 256-MiB tmpfs filled with WAL and PostgreSQL exited. Container logs explicitly report `No space left on device`; this is not counted as a passing run. The failed disposable container was removed after saving logs, and the rerun uses a task-specific disk-backed directory.
- The first disk-backed startup failed because the task directory's mode 0700 prevented the PostgreSQL user from traversing it. Mode 0755 on that synthetic test directory fixed startup; its database files retain PostgreSQL's restricted permissions. Readiness subsequently passed and the restarted test run connected successfully.
- Coverage is broad but not the complete repository CI matrix. No GitHub PR status, CodeRabbit thread, mergeability, release packaging, full production-image build, Helm/Nix validation, or production load test is claimed. Browser smoke is functional verification, not a new PR's before/after screenshot set.
- Existing SQLite expression-index reflection warnings, Starlette deprecation warnings, jsdom scroll/canvas limitations, and Vite plugin advice were observed; they did not fail the final listed checks.

## Handoff state

The merge remains pending a separately authorized commit, push, and deploy. Recovery stash and test logs are retained; do not reapply the stash blindly because its changes are already integrated. Production continues on its existing deployment.

The disposable PostgreSQL container was stopped and removed after verification.
Its final synthetic data directory remains at `/tmp/codex-lb-beta6-pg.21trfw`;
the native verification image and extracted helper remain available for reruns.
