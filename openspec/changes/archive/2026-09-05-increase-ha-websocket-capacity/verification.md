# Verification: increase-ha-websocket-capacity

Date: 2026-09-05. Scope: local implementation and validation only; production deploy, commit and push were not performed.

## Completeness and correctness

- Native byte budgets: `native_buffer.py` and `native_egress.py` share connection/helper accounting across raw and decoded queues, yield in bounded batches, preserve accepted prefixes on pressure, isolate per-socket cancellation and release abandoned/closed buffers. Tests cover 65-message bursts, binary/close compatibility, 100/300/500 concurrent sockets, delayed consumption with send acknowledgements, per-connection/global overflow and shutdown waking an already-waiting receiver.
- Failure policy: `proxy_websocket.py` emits account-neutral `proxy_websocket_buffer_exhausted`. Direct relay tests verify no replay, no account penalty and one reservation release; HTTP-bridge tests verify neutral settlement and no replay. An integration test on `/backend-api/codex/responses` verifies the terminal response envelope and no retry/health penalty after response.created.
- HA profile: four private services, three base identities, 3-GiB container limits, 1-GiB queue overrides, two pools of (8 + 2) per process and unchanged public port. Compose renders successfully; resource tests establish 12-GiB/80-connection maximum candidate overlap.
- Rollout: fake-Docker tests cover ordering, legacy runtime registration, per-slot replacement failures/resume, amber rollback, retained live surge, refusal to recreate unrecorded live surge, recovery of an already-readmitted candidate, reload failure/recovery, old workers and unreadable drain counters. No direct production container recreation or runtime mutation was used for validation.
- Documentation: main specs and context synchronized; published deployment and generated settings pages updated; deployment skill validated, authorization boundaries and bounded connection lifetime retained.

## Executed checks

- Final native/adapter/packaging/observability group: 107 passed.
- Final HA + selected direct-relay/HTTP-bridge/integration failure group: 37 passed, 2297 unrelated tests deselected.
- Settings-reference and multi-replica settings group: 49 passed.
- Earlier broader regression across native, HA, HTTP-bridge and WebSocket integration: 1218 passed, one pre-existing failure (below). Subsequent targeted groups cover the final changes.
- Ruff lint and format for all changed Python files: pass. Targeted ty check: pass. Bash syntax, Compose render and `git diff --check`: pass.
- Change and both owning main specs: strict validation passes. Deployment skill quick validation: pass.
- Actual pinned HAProxy 3.2 image: checked-in configuration passes syntax validation in a network-isolated container (expected unresolved-backend notices). Runtime `add server .../amber ... id 4 ... weight 0` accepts the new member.
- Isolated graceful reload: master PID remains 1, worker changes from 8 to 46, old worker 8 retains an open TCP connection, and a separate readiness frontend returns 200. Both temporary HAProxy containers were removed after checks. No host ports or production networks were attached.

## Mock workload measurements and limits

One run used 100/300/500 sockets over the fake subprocess helper, each receiving 128 text messages plus completion, with consumers delayed until all sends were acknowledged. Test-call durations were 0.28/0.88/1.18 seconds. All 900 sockets delivered their complete ordered event sequence. Peak RSS reported for the entire pytest invocation was 189556 KiB; this is not per-backend production RSS. The environment's actual `.venv/bin/python` is CPython 3.14.7, despite the older environment note in AGENTS.

These are queue/helper regression measurements, not an API throughput guarantee: no real upstream latency, account quotas, PostgreSQL workload, TLS or billing is modeled. Production CPU/event-loop lag/RSS/pool wait/first-token latency and mixed-version connection use must still be observed during an authorized deployment/load test. The 80 DB connection bound applies to candidate settings, not unreplaced legacy replicas.

## Baseline issues independently reproduced

- `test_stream_via_http_bridge_fails_closed_before_file_affinity_when_previous_response_owner_misses` fails before its assertion because SQLite lacks `file_account_pins`. Reproduced both in the modified tree and a detached worktree at baseline `fbacfd36`; that temporary worktree was removed. No unrelated file-ownership or migration code was changed.
- Global strict OpenSpec validation: 36 specs pass and 23 fail, identically on baseline `fbacfd36` and the changed tree. Most failures are historical Purpose placeholders; `model-source-routing` also has existing structural issues. Both affected capabilities and this change pass strict validation. These unrelated spec failures were not rewritten.

## Coherence and handoff

Implementation follows the byte-budget, account-neutral and 3+1 design. The skill keeps the tested script as the sole deployment mutation path. The change is verified for local implementation; production adoption remains a separate explicit operator action. Do not claim all tests/specs globally green or absolute zero long-lived connection loss.
