# HTTP Bridge Quarantine and Soft Affinity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound silent HTTP bridge failures to five seconds, quarantine the failed bridge for sixty seconds with same-owner HTTP fallback, and keep bare session-header affinity movable without weakening real upstream ownership.

**Architecture:** Reuse the current eventless-reader retirement path as the single resource-cleanup owner. Add a bounded per-session quarantine registry checked before bridge acquisition, then route quarantined retries through the existing HTTP streaming path. Preserve hard ownership for turn-state, `previous_response_id`, conversations, and uploaded files; only a bare session-header bridge key may be soft.

**Tech Stack:** Python 3.13, asyncio/anyio, FastAPI, SQLAlchemy, pytest/pytest-asyncio, Ruff, ty.

---

### Task 1: Preserve the migrated baseline in the isolated branch

**Files:**
- Existing baseline changes under `app/` and `tests/`
- Create: `docs/superpowers/plans/2026-07-26-bridge-quarantine-soft-affinity.md`

- [ ] **Step 1: Verify the imported baseline matches `repo-upstream-migration`**

Run a SHA-256 comparison for every changed tracked file and the two migration files, excluding `.codegraph`.

Expected: every source/target pair has the same hash.

- [ ] **Step 2: Run the existing bridge and affinity baseline**

Run:

```powershell
uv run pytest -q `
  tests/unit/test_proxy_http_bridge.py::test_http_bridge_eventless_precreated_deadline_uses_current_send_and_client_safe_cap `
  tests/unit/test_proxy_http_bridge.py::test_http_bridge_eventless_precreated_deadline_requires_narrow_owner_evidence `
  tests/unit/test_proxy_http_bridge.py::test_http_bridge_reader_wakes_and_retires_lone_eventless_owner_without_keepalives `
  tests/unit/test_proxy_http_bridge.py::test_http_bridge_eventless_timeout_yields_to_locked_send_failure_cleanup `
  tests/unit/test_proxy_http_bridge.py::test_http_bridge_eventless_timeout_force_retires_with_admission_waiter `
  tests/unit/test_proxy_utils.py::test_select_account_with_budget_intersects_cap_spillover_with_request_stage
```

Expected: PASS.

- [ ] **Step 3: Commit the isolated baseline**

Commit only the imported migration/local-retained changes and this plan with a Lore-format message. Do not modify or commit the original `repo-upstream-migration` worktree.

### Task 2: Add a five-second response-created deadline and quarantine registry

**Files:**
- Modify: `app/core/config/settings.py`
- Modify: `app/modules/proxy/service.py`
- Modify: `app/modules/proxy/_service/http_bridge/protocol.py`
- Modify: `app/modules/proxy/_service/http_bridge/mixin.py`
- Modify: `app/modules/proxy/_service/http_bridge/helpers.py`
- Modify: `app/modules/proxy/_service/http_bridge/upstream_events.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`

- [ ] **Step 1: Write failing deadline tests**

Add tests proving:

```python
settings.http_responses_session_bridge_response_created_timeout_seconds == 5.0
settings.http_responses_session_bridge_quarantine_seconds == 60.0
```

and proving `_http_bridge_eventless_precreated_deadline()` uses the dedicated response-created timeout rather than the 240-second compatibility cap.

- [ ] **Step 2: Run the new tests and verify RED**

Expected failures: settings are absent and the eventless deadline remains 240 seconds for the default stuck-gate input.

- [ ] **Step 3: Add the minimal configuration and deadline wiring**

Add:

```python
http_responses_session_bridge_response_created_timeout_seconds: float = Field(default=5.0, gt=0)
http_responses_session_bridge_quarantine_seconds: float = Field(default=60.0, ge=0)
```

Pass the dedicated timeout into the existing eventless deadline calculation. Keep `http_responses_session_bridge_stuck_gate_retire_after_seconds` for older non-eventless stuck-gate behavior.

- [ ] **Step 4: Write failing quarantine lifecycle tests**

Cover:

```python
service._http_bridge_quarantine_until[key] > monotonic_now
session.closed is True
session not in service._http_bridge_sessions.values()
request_state.response_create_gate_acquired is False
request_state.account_response_create_lease is None
request_state.websocket_stream_lease is None
not session.pending_requests
```

Also assert quarantine storage is bounded by `http_responses_session_bridge_max_sessions` and is cleared during bridge shutdown.

- [ ] **Step 5: Run quarantine tests and verify RED**

Expected failure: `_http_bridge_quarantine_until` and quarantine registration do not exist.

- [ ] **Step 6: Implement quarantine on the existing forced-retirement path**

Initialize:

```python
self._http_bridge_quarantine_until: dict[_HTTPBridgeSessionKey, float] = {}
```

When `_HTTP_BRIDGE_MISSING_RESPONSE_CREATED_TIMEOUT_DETAIL` wins the reader timeout:

1. record `now + quarantine_seconds` under the bridge key;
2. mark the current request as an upstream timeout;
3. cancel the pending receive;
4. call the existing `_fail_http_bridge_reader_and_maybe_retire(..., force_retire=True)`;
5. do not invoke any replay helper for the current request.

Prune expired entries and cap the registry at `http_responses_session_bridge_max_sessions`.

- [ ] **Step 7: Run the Task 2 tests and verify GREEN**

Expected: all Task 2 tests pass without warnings or leaked background tasks.

- [ ] **Step 8: Commit Task 2**

Commit the tests and implementation with a Lore-format message documenting the no-replay constraint.

### Task 3: Route quarantined retries through HTTP while preserving the owner

**Files:**
- Modify: `app/modules/proxy/_service/http_bridge/streaming.py`
- Modify: `tests/integration/test_http_responses_bridge.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`

- [ ] **Step 1: Add failing HTTP-fallback tests**

Adapt the old `ba841b5d` contracts:

```python
test_backend_http_bridge_quarantines_silent_session_then_uses_http_without_internal_replay
test_backend_http_bridge_quarantine_preserves_previous_response_account_on_http_retry
```

Assert:

- the first request sends one `response.create`, fails near the configured timeout, and is never replayed internally;
- the failed websocket is closed;
- the next client retry bypasses bridge creation and uses upstream HTTP;
- `previous_response_id` resolves the original account and no alternate account is selected;
- after quarantine expiry, bridge routing becomes eligible again.

- [ ] **Step 2: Run the fallback tests and verify RED**

Expected failure: the retry attempts to acquire/reconnect a bridge instead of selecting HTTP fallback.

- [ ] **Step 3: Add the quarantine lookup and fallback**

Before local/durable bridge acquisition, compute remaining quarantine time for both the initial key and any canonical durable key. If positive, call the existing `_stream_with_retry()` path with:

```python
request_transport="http"
upstream_stream_transport_override="http"
```

Pass the original payload, headers, API-key context, reservation, file-owner rewrite, client IP, and SDK contract flags unchanged. Let the existing direct-stream continuity resolver enforce the original account for `previous_response_id`.

- [ ] **Step 4: Verify GREEN**

Run both fallback tests plus existing previous-response owner tests.

Expected: quarantined retry uses HTTP on the same owner; current request is not replayed.

- [ ] **Step 5: Commit Task 3**

Use a Lore-format message stating that quarantine changes transport only, not ownership.

### Task 4: Restore resource-lifetime regression coverage from `f389c2f3` and `96ceb931`

**Files:**
- Modify: `tests/unit/test_proxy_utils.py`
- Modify only if tests fail: `app/modules/proxy/_service/http_bridge/request_submit.py`
- Modify only if tests fail: `app/modules/proxy/_service/http_bridge/upstream_events.py`
- Modify only if tests fail: `app/modules/proxy/service.py`

- [ ] **Step 1: Restore failing-or-passing race tests**

Port the behavioral assertions, not the old implementation:

```python
test_http_bridge_stream_cap_failure_releases_response_create_gate_before_wait
test_http_bridge_close_and_terminal_cleanup_release_transferred_lease_once
test_http_bridge_prewarm_timeout_releases_all_account_pressure
test_http_bridge_client_disconnect_cleans_generator_pending_and_gate
```

- [ ] **Step 2: Run each test independently**

If a test passes immediately, record that the newer implementation already satisfies the invariant and make no production change for it. If it fails for the intended reason, keep the red test and proceed.

- [ ] **Step 3: Apply only minimal resource-lifetime fixes required by RED tests**

Required invariants:

- stream leases count only while a request is active;
- response-create gate/lease is released before capacity waiting;
- close and terminal cleanup release a lease exactly once;
- silent prewarm at stream cap 1 ends with pressure `(0, 0, 0.0)`;
- generator close removes pending state and releases gate/leases.

- [ ] **Step 4: Run focused tests and verify GREEN**

Expected: all restored resource tests pass and account pressure returns to zero.

- [ ] **Step 5: Commit Task 4**

Commit tests and any required minimal fixes. Note in the Lore trailers which old behaviors were already present.

### Task 5: Keep bare session-header bridge affinity soft

**Files:**
- Modify: `app/modules/proxy/_service/http_bridge/helpers.py`
- Modify: `app/modules/proxy/_service/support.py` only if key representation needs a helper
- Modify: `tests/unit/test_proxy_http_bridge.py`
- Verify: `tests/unit/test_load_balancer_concurrency.py`
- Verify: `tests/unit/test_proxy_utils.py`

- [ ] **Step 1: Add failing bridge-key classification tests**

Add a parameterized test proving:

```python
bare_session_header_without_owner.strength == "soft"
session_header_with_previous_response_id.strength == "hard"
session_header_with_conversation.strength == "hard"
session_header_with_input_file.strength == "hard"
turn_state_header.strength == "hard"
prompt_cache.strength == "soft"
```

- [ ] **Step 2: Run the classification test and verify RED**

Expected failure: every session-header key is currently constructed with `strength="hard"`.

- [ ] **Step 3: Implement source-aware strength**

In `_make_http_bridge_session_key()`, assign session-header strength from the already-derived affinity capability:

```python
strength = "soft" if affinity.codex_session_source == "session_header" and affinity.spill_on_account_cap else "hard"
```

Do not infer mobility from key text. Do not alter turn-state, durable previous-response, conversation, or file-owner resolution.

- [ ] **Step 4: Add/verify spill and ownership tests**

Verify these existing contracts:

```python
test_bare_codex_session_spills_without_rebinding_when_owner_reaches_account_cap
test_legacy_raw_session_mapping_remains_hard_during_upgrade
test_legacy_raw_session_mapping_wins_when_namespaced_row_also_exists
test_stream_prompt_cache_key_does_not_soften_previous_response_owner
test_stream_previous_response_owner_miss_fails_closed_before_unpinned_selection
```

Add:

```python
test_stream_via_http_bridge_hard_affinity_saturated_fails_fast_without_capacity_wait
```

The fast-fail test must assert `_iter_account_capacity_wait_sse` is never called and no response-create gate is acquired.

- [ ] **Step 5: Run Task 5 tests and verify GREEN**

Expected: bare session-header may select a temporary alternate account without overwriting the original sticky mapping; all owner-bearing requests remain fail-closed.

- [ ] **Step 6: Commit Task 5**

Use a Lore-format message stating that typed provenance grants mobility while stored-object ownership remains hard.

### Task 6: Static and regression verification

**Files:**
- All changed production and test files

- [ ] **Step 1: Run targeted suites**

Run:

```powershell
uv run pytest -q tests/unit/test_proxy_http_bridge.py
uv run pytest -q tests/unit/test_load_balancer_concurrency.py
uv run pytest -q tests/unit/test_proxy_utils.py
uv run pytest -q tests/integration/test_http_responses_bridge.py
```

- [ ] **Step 2: Run code-quality gates**

Run:

```powershell
uv run ruff check <changed-python-files>
uv run ruff format --check <changed-python-files>
uv run ty check --python-platform linux
uv run python scripts/check_proxy_architecture.py
git diff --check
```

Expected: zero errors.

- [ ] **Step 3: Perform specification and code-quality reviews**

Run a fresh spec-compliance review, then a fresh code-quality review. Fix every important issue and re-run the relevant verification.

### Task 7: Isolated runtime verification on port 2456

**Files:**
- Runtime only; do not modify production configuration or processes

- [ ] **Step 1: Prove production is untouched**

Record the current PID and health status of `127.0.0.1:2455` and the GPT-5.6 shim before starting the candidate.

- [ ] **Step 2: Start the candidate on 2456**

Use an isolated database copy or temporary SQLite database and the new worktree:

```powershell
uv run codex-lb --host 127.0.0.1 --port 2456
```

Do not call `restart-codex-lb.ps1`; do not stop 2455 or 15956.

- [ ] **Step 3: Run runtime smoke tests**

Verify:

- `/health/live` returns 200;
- `/dashboard` returns HTML after building frontend assets if needed;
- `/backend-api/codex/alpha/search` is routed (401 without auth is acceptable);
- a controlled silent bridge fixture fails near five seconds;
- its next retry uses HTTP during quarantine;
- a separate session/account is not blocked;
- hard owner saturation returns quickly rather than waiting for a gate.

- [ ] **Step 4: Stop only the 2456 candidate**

Resolve and stop the PID that owns port 2456. Re-check that the original 2455 PID and health status are unchanged.

- [ ] **Step 5: Report without deploying**

Report changed files, test counts, smoke evidence, remaining risks, and the candidate branch/worktree. Explicitly state that production 2455 was not replaced.
