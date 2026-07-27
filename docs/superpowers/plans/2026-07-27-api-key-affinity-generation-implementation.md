# API-Key Account-Pool Affinity Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to execute this plan task by task.

**Goal:** Make a Dashboard account-pool change cut over ordinary API-key sessions to the new pool without stale hard-affinity failures, while preserving true upstream continuity for at most 30 minutes.

**Architecture:** Persist a monotonic assignment generation on each API key and include it in every soft session-affinity and bridge session-header key. Treat `previous_response_id`, real turn state, and equivalent owner evidence as hard continuity; allow only those proven owners to drain outside the new pool for a bounded interval. Keep generation `1` compatible with existing affinity rows, activate strict generation isolation only after the first real pool change, and preserve request-surface-specific replay rules.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async ORM, Alembic, SQLite/PostgreSQL, pytest, Ruff, ty, existing HTTP/WebSocket bridge and sticky-session repositories.

---

## Task 1: Persist API-key assignment generations

**Files:**

- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/20260727_000000_add_api_key_assignment_generation.py`
- Modify: `app/modules/api_keys/service.py`
- Modify: `app/core/config/settings.py`
- Test: `tests/integration/test_migrations.py`
- Test: `tests/unit/test_api_keys_service.py`
- Test: `tests/unit/test_settings_reference.py`

- [x] Add failing migration and conversion tests proving existing keys receive generation `1`, a nullable change timestamp, and `ApiKeyData` exposes both fields.

```python
assert data.account_assignment_generation == 1
assert data.account_assignment_changed_at is None
```

- [x] Run the focused tests and confirm they fail because the columns and dataclass fields do not exist.

```powershell
uv run pytest -q tests/integration/test_migrations.py tests/unit/test_api_keys_service.py -k "assignment_generation"
```

Expected: failing assertions or constructor errors naming `account_assignment_generation`.

- [x] Add the ORM columns and migration:

```python
account_assignment_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
account_assignment_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

The migration must revise `20260726_000000_merge_account_concurrency_overrides_and_main`, add both columns idempotently, and remove them in reverse order during downgrade.

- [x] Add immutable runtime fields to `ApiKeyData` and copy them through `_to_api_key_data` and `_to_created_data`:

```python
account_assignment_generation: int = 1
account_assignment_changed_at: datetime | None = None
```

- [x] Add the bounded drain setting near the existing affinity settings:

```python
api_key_account_assignment_drain_seconds: int = Field(default=1800, ge=0)
```

- [x] Re-run the focused tests.

```powershell
uv run pytest -q tests/integration/test_migrations.py tests/unit/test_api_keys_service.py tests/unit/test_settings_reference.py -k "assignment_generation or assignment_drain"
```

Expected: all selected tests pass.

## Task 2: Increment the generation only for a real serialized pool change

**Files:**

- Modify: `app/modules/api_keys/repository.py`
- Modify: `app/modules/api_keys/service.py`
- Modify: `tests/unit/test_api_keys_service.py`
- Modify: `tests/integration/test_api_keys_api.py`

- [x] Add failing tests for:

  - reordered or duplicate account IDs do not increment;
  - changing another API-key field does not increment;
  - changing the normalized set increments exactly once and records `utcnow()`;
  - disabling or enabling assignment scope with the same effective set follows the spec’s normalized-set rule;
  - two concurrent writers cannot both commit the same successor generation.

```python
assert updated.account_assignment_generation == original.account_assignment_generation + 1
assert updated.account_assignment_changed_at is not None
```

- [x] Run the focused tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_api_keys_service.py tests/integration/test_api_keys_api.py -k "assignment_generation or concurrent_assignment"
```

- [x] Add a repository transaction entry point that serializes the compare-and-replace operation:

```python
async def replace_account_assignments_if_changed(
    self,
    key_id: str,
    account_ids: list[str],
    *,
    changed_at: datetime,
) -> bool:
    normalized_ids = sorted(set(account_ids))
    row = await self._get_api_key_for_assignment_update(key_id)
    if row is None:
        return False
    existing_ids = sorted(assignment.account_id for assignment in row.account_assignments)
    if existing_ids == normalized_ids:
        return False
    row.account_assignment_generation += 1
    row.account_assignment_changed_at = changed_at
    await self._replace_account_assignments_unlocked(key_id, normalized_ids)
    return True
```

On PostgreSQL, select the `ApiKey` row with `FOR UPDATE`. On SQLite, hold the existing `sqlite_writer_section()` for the complete read/compare/replace/update transaction. Compare sorted unique ID sets, increment `account_assignment_generation`, set `account_assignment_changed_at`, replace assignments, and commit once.

- [x] Change `ApiKeysService.update_key` to call this method only when `assigned_account_ids` was supplied, retain the surrounding rollback behavior, and invalidate the local/distributed API-key caches only after commit.

- [x] Re-run both focused test files.

```powershell
uv run pytest -q tests/unit/test_api_keys_service.py tests/integration/test_api_keys_api.py
```

Expected: both files pass.

## Task 3: Namespace soft affinity by API key and generation

**Files:**

- Modify: `app/modules/proxy/affinity.py`
- Modify: `app/modules/proxy/service.py`
- Modify: `tests/unit/test_proxy_utils.py`
- Modify: `tests/unit/test_load_balancer_concurrency.py`

- [x] Add failing tests proving:

  - generation `1` session-header requests preserve the current v1 selection key and raw legacy lookup;
  - generation `2+` session-header requests use a key containing the API-key ID and generation and do not expose a raw legacy key;
  - prompt-cache and sticky-thread soft keys change when the generation changes;
  - real turn-state keys do not change and remain hard;
  - identical client session headers under different API keys do not collide.

```python
assert generation_two.key != generation_one.key
assert generation_two.legacy_selection_key is None
assert generation_two.codex_session_source == "session_header"
```

- [x] Run the selected tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_proxy_utils.py tests/unit/test_load_balancer_concurrency.py -k "assignment_generation or legacy_session_header or soft_affinity"
```

- [x] Add one canonical soft-key builder:

```python
def api_key_soft_affinity_key(
    raw_key: str,
    *,
    kind: StickySessionKind,
    source: str | None,
    api_key: ApiKeyData | None,
) -> str:
    api_key_id = api_key.id if api_key is not None else "anonymous"
    generation = api_key.account_assignment_generation if api_key is not None else 1
    raw_digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    identity = "\0".join(("v2", api_key_id, str(generation), kind.value, source or "", raw_digest))
    return f"\ncodex-lb-affinity-v2:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
```

For generation `2+`, hash a stable payload containing schema version, API-key ID (or the anonymous scope), generation, affinity kind, source, and the raw-key digest. Keep real turn-state and previous-response ownership outside this function.

- [x] Update `_sticky_key_for_responses_request`, bare session-header handling, prompt-cache handling, and `preferred_owner_sticky_inputs` so generation `2+` session-header traffic cannot consult the old raw `CODEX_SESSION` row.

- [x] Integrate the existing continuity-owner conflict fix from commit `4a0984f5` by behavior, not by blind cherry-pick: stale soft affinity must not reject a separately proven hard owner.

- [x] Re-run the full focused files.

```powershell
uv run pytest -q tests/unit/test_proxy_utils.py tests/unit/test_load_balancer_concurrency.py
```

Expected: both files pass.

## Task 4: Version HTTP bridge session-header identity and durable aliases

**Files:**

- Modify: `app/modules/proxy/_service/support.py`
- Modify: `app/modules/proxy/_service/http_bridge/helpers.py`
- Modify: `app/modules/proxy/_service/http_bridge/session_registry.py`
- Modify: `app/modules/proxy/durable_bridge_coordinator.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`
- Modify: `tests/integration/test_http_responses_bridge.py`

- [x] Add failing tests proving:

  - generation `2+` bridge fallback keys differ from generation `1`;
  - forwarded bridge affinity preserves the already-versioned key;
  - generation `2+` durable `session_header` lookup cannot resolve a generation `1` alias;
  - `previous_response_id` and turn-state aliases still resolve the old hard owner;
  - two API keys with the same session header remain isolated.

- [x] Run the focused tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py -k "assignment_generation or durable_session_header_alias"
```

- [x] Add the generation to `_HTTPBridgeSessionKey` and build the session-header alias from the same canonical soft-key helper used by direct routing:

```python
_HTTPBridgeSessionKey(
    affinity_kind="session_header",
    affinity_key=versioned_session_header_key,
    api_key_id=api_key.id,
    account_assignment_generation=api_key.account_assignment_generation,
    strength="soft",
)
```

- [x] Register and resolve only the versioned session-header alias for generation `2+`. Retain the unversioned alias path exclusively for generation `1`; do not version turn-state or previous-response aliases.

- [x] Ensure forwarded bridge requests carry the full versioned affinity key unchanged and cannot silently reconstruct a different generation on the destination instance.

- [x] Re-run both focused files.

```powershell
uv run pytest -q tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py
```

Expected: both files pass.

## Task 5: Add the bounded hard-owner drain path

**Files:**

- Modify: `app/modules/proxy/service.py`
- Modify: `app/modules/proxy/load_balancer.py`
- Modify: `app/modules/proxy/_service/http_bridge/helpers.py`
- Modify: `tests/unit/test_load_balancer_concurrency.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`

- [x] Add failing tests covering:

  - a proven hard owner outside the new pool remains eligible inside the 30-minute window;
  - the same owner is rejected after the window;
  - setting the window to zero disables drain;
  - a soft session-header owner never receives the bypass;
  - an inactive, rate-limited, model-incompatible, or missing hard owner fails immediately;
  - drain-only owners never become candidates for unrelated new work.

- [x] Run the selected tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_load_balancer_concurrency.py tests/unit/test_proxy_http_bridge.py -k "assignment_drain or hard_owner_outside_scope"
```

- [x] Add a pure eligibility helper:

```python
def api_key_hard_owner_drain_active(
    api_key: ApiKeyData | None,
    *,
    now: datetime,
    drain_seconds: int,
) -> bool:
    if (
        api_key is None
        or api_key.account_assignment_generation <= 1
        or api_key.account_assignment_changed_at is None
        or drain_seconds <= 0
    ):
        return False
    deadline = api_key.account_assignment_changed_at + timedelta(seconds=drain_seconds)
    return now < deadline
```

It returns true only when generation is greater than `1`, the change timestamp exists, the configured duration is positive, and `now` is before the deadline.

- [x] Extend `select_account` with an explicit required-owner-only scope bypass. Load the out-of-scope account only when `required_continuity_owner=True` and drain is active; never union it into the ordinary candidate pool.

```python
allow_required_owner_outside_account_ids: bool = False
```

- [x] Propagate this flag from direct responses, compact, streaming, and HTTP bridge selection only after hard ownership is proven by typed evidence.

- [x] Ensure a saturated or unavailable hard owner returns immediately without entering a long capacity gate wait.

- [x] Re-run the focused files.

```powershell
uv run pytest -q tests/unit/test_load_balancer_concurrency.py tests/unit/test_proxy_http_bridge.py
```

Expected: both files pass.

## Task 6: Make post-cutover errors and replay behavior surface-specific

**Files:**

- Modify: `app/modules/proxy/service.py`
- Modify: `app/modules/proxy/_service/http_bridge/request_submit.py`
- Modify: `app/modules/proxy/_service/http_bridge/streaming.py`
- Modify: `app/modules/proxy/_service/http_bridge/protocol.py`
- Modify: `tests/unit/test_proxy_utils.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`
- Modify: `tests/integration/test_http_responses_bridge.py`

- [ ] Add failing tests proving:

  - a portable pre-ack request may replay at most once on an in-pool account;
  - any request with `previous_response_id`, real turn state, file ownership, or post-ack side effects never crosses accounts;
  - a non-portable unavailable owner returns `continuity_reset_required`;
  - `hard_affinity_saturated` is reserved for a healthy hard owner whose concurrency is genuinely exhausted;
  - client disconnect clears the bridge generator, pending request, response-create lease, stream lease, and gate.

- [ ] Run the focused tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_proxy_utils.py tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py -k "continuity_reset_required or portable_replay or post_ack_cleanup"
```

- [ ] Keep replay decisions in the owning request surface. Add a single-attempt pre-ack guard and translate owner failures without stripping hard references:

```python
if hard_owner_required and owner_unavailable:
    raise ProxyResponseError(
        502,
        openai_error(
            "continuity_reset_required",
            "The previous upstream conversation is no longer available after the account-pool change.",
            error_type="server_error",
        ),
    )
```

- [ ] Preserve the previously restored five-second silent-bridge quarantine and HTTP fallback behavior. Current requests must not be replayed after `response.create` may have reached upstream.

- [ ] Re-run the focused files.

```powershell
uv run pytest -q tests/unit/test_proxy_utils.py tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py
```

Expected: all three files pass.

## Task 7: Add cutover observability without leaking session material

**Files:**

- Modify: `app/modules/proxy/observability.py`
- Modify: `app/modules/proxy/service.py`
- Modify: `app/modules/proxy/_service/http_bridge/helpers.py`
- Modify: `tests/unit/test_metrics.py`
- Modify: `tests/unit/test_request_log_upstream_proxy_metadata.py`
- Modify: `tests/unit/test_proxy_http_bridge.py`

- [ ] Add failing tests for structured fields and counters:

  - `api_key_assignment_generation`;
  - `affinity_source`;
  - `affinity_strength`;
  - `assignment_cutover_result` with `new_pool`, `hard_drain`, `reset_required`, or `hard_saturated`;
  - no raw session header, prompt-cache key, turn state, or previous-response ID.

- [ ] Run the focused tests and confirm red.

```powershell
uv run pytest -q tests/unit/test_metrics.py tests/unit/test_request_log_upstream_proxy_metadata.py tests/unit/test_proxy_http_bridge.py -k "assignment_cutover_observability"
```

- [ ] Emit bounded labels and hashed/redacted identifiers only. Reuse existing request logging and metric helpers instead of creating a parallel telemetry subsystem.

- [ ] Re-run the focused tests.

```powershell
uv run pytest -q tests/unit/test_metrics.py tests/unit/test_request_log_upstream_proxy_metadata.py tests/unit/test_proxy_http_bridge.py
```

Expected: both files pass.

## Task 8: Verify the complete change and smoke-test on port 2456

**Files:**

- Modify if required: `.env.example`
- Modify if required: `README.md`
- Verify: all changed Python files and relevant test suites

- [ ] Document `CODEX_LB_API_KEY_ACCOUNT_ASSIGNMENT_DRAIN_SECONDS=1800` in the existing configuration reference if that reference enumerates affinity settings.

- [ ] Run formatting, lint, type checking, migration checks, architecture checks, and the complete related test matrix.

```powershell
uv run ruff format --check app tests
uv run ruff check app tests
uv run ty check --python-platform linux
uv run pytest -q tests/integration/test_migrations.py tests/unit/test_api_keys_service.py tests/integration/test_api_keys_api.py tests/unit/test_proxy_utils.py tests/unit/test_load_balancer_concurrency.py tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py tests/unit/test_metrics.py tests/unit/test_request_log_upstream_proxy_metadata.py
uv run python scripts/check_proxy_architecture.py
git diff --check
```

Expected: every command exits `0`.

- [ ] Start only the isolated candidate on `127.0.0.1:2456` using a verified copy of the production SQLite database and the production encryption key. Do not stop or modify port `2455`.

```powershell
.\start-codex-lb.ps1 -RepoRoot 'F:\agent workspace\codex-lb\repo-upstream-migration' -BindHost 127.0.0.1 -Port 2456 -SkipStopExisting -SkipCodex56ToolsProxy
```

- [ ] Verify:

  - `/health/live` returns `200`;
  - `/dashboard` returns HTML `200`;
  - migration head is `20260727_000000_add_api_key_assignment_generation`;
  - a no-op account reorder leaves generation unchanged;
  - a real account-pool change increments generation once;
  - a new soft session routes only inside the new pool;
  - a hard previous-response owner drains on its original account inside the window;
  - an unavailable hard owner fails in seconds, not minutes;
  - no raw session or continuity values appear in logs.

- [ ] Stop the isolated `2456` process and confirm production `2455` remained healthy throughout.

- [ ] Run the final diff and worktree review. Exclude `.codegraph/` and `.temp-untracked-backup/` from commits.

```powershell
git status --short --branch
git diff --stat
git diff --check
```

- [ ] Commit implementation in small Lore-protocol commits and prepare, but do not execute, the production `2455` replacement command.

## Completion Gate

The implementation is complete only when all eight tasks pass, the isolated `2456` smoke test demonstrates both soft cutover and hard drain behavior, port `2455` was not touched, and the final report lists changed files, commit IDs, verification evidence, and any remaining rollout risk.
