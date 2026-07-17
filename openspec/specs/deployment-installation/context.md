# Deployment & Installation Context

## Purpose and Scope

This capability owns the install contracts (Helm modes, Compose profiles,
smoke tests) and the operator environment-variable contract at
settings-load time: which `CODEX_LB_*` values exist, which are deliberately
fixed, and how removed settings are retired.

See `openspec/specs/deployment-installation/spec.md` for normative
requirements.

## NEXT-RELEASE QUEUE (do not lose)

Work queued for the release after the one that shipped the
settings-surface reduction (issue #1340, phases 1-4 + retention dashboard
settings, merged as PRs #1351, #1360, #1362, #1363, #1364 in v1.21.x):

1. **Drop the deprecated prewarm request-log columns.** `RequestLog`
   still declares `prewarm_canary_bucket` and `prewarm_eligible_reason`
   (deprecated, unwritten since phase 4) so old replicas keep inserting
   safely during rolling upgrades — the Helm migration job is a
   pre-upgrade hook while the workload rolls. The Alembic drop revision
   MUST ship in the next release; it could not ship together with the
   writer removal.
2. **Retire the retention env aliases.**
   `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` and
   `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` are deprecated one-release
   aliases for the dashboard retention settings
   (`retention-dashboard-settings`). A follow-up phase removes the env
   fields and adds them to `_REMOVED_SETTINGS` with a pointer to the
   dashboard setting, once operators have had a release to migrate. See
   `openspec/specs/data-retention/context.md`.
3. **Eventually retire the removal warning itself.** `_REMOVED_SETTINGS`
   and `warn_removed_settings()` in `app/core/config/settings.py` are a
   one-release courtesy per removed batch ("at least one release"); prune
   entries (or the mechanism) once every batch has had its warning
   release. Item 2 adds entries first, so this comes last.

## Settings-surface reduction rationale (issue #1340, phases 1-4)

PRINCIPLES.md P2: "a setting the operator never needs to touch is a
default in disguise." The `Settings` class carried 165 env-settable fields
before phase 1; phases 1-4 removed 52 of them (plus adding `CODEX_LB_TRACE`).
Selection rule for every phase: removal is provably zero-risk — each
removed field keeps its exact previous default as the new fixed value, so
behavior is byte-identical for any install that never overrode it, and the
only behavioral seam (the removed-settings warning) is additive.

Capability choice: `deployment-installation` owns the operator env-var
contract at settings-load time (see the data-directory resolution
requirement), so the fixed-constants + removal-warning requirement lives
here rather than in `contribution-simplicity`, which governs the
contribution/review process, not runtime behavior.

### Removed fields by phase

Phase 1 (24 removed, 1 added; zero-risk internals):

- OAuth protocol identity (6): `CODEX_LB_AUTH_BASE_URL`
  (`https://auth.openai.com`), `CODEX_LB_OAUTH_CLIENT_ID`
  (`app_EMoamEEZ73f0CkXaXp7hrann`), `CODEX_LB_OAUTH_ORIGINATOR`
  (`codex_chatgpt_desktop`), `CODEX_LB_OAUTH_SCOPE`
  (`openid profile email`), `CODEX_LB_OAUTH_REDIRECT_URI`
  (`http://localhost:1455/auth/callback`), `CODEX_LB_OAUTH_CALLBACK_PORT`
  (1455) — module constants in `app/core/config/settings.py`; changing any
  of them breaks login.
- Auth guardian tuning (7): interval 21600, max refresh age 43200, batch
  size 100, concurrency 3, jitter 300.0, failure backoff base 300.0 / max
  3600.0 — constants in `app/core/auth/guardian.py`;
  `CODEX_LB_AUTH_GUARDIAN_ENABLED` remains the single switch.
- Debug log booleans (6): the `CODEX_LB_LOG_PROXY_*` /
  `CODEX_LB_LOG_UPSTREAM_*` booleans became `CODEX_LB_TRACE` channels
  (`shape`, `shape_raw_cache_key`, `payload`, `service_tier`,
  `upstream_summary`, `upstream_payload`); empty default = all off. This
  is an incident-debugging knob for interactive use only; there is no
  correct steady-state value other than "off".
- Bulkhead per-class overrides (3): http/websocket/compact limits always
  derive from `CODEX_LB_BULKHEAD_PROXY_LIMIT` (http = websocket = proxy
  limit; compact = min(http, 16), 0 when http is 0).
- Token-refresh claim polling (2): wait 8.0 s, poll 0.25 s — constants in
  `app/modules/accounts/auth_manager.py`.
  `CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS` stays: its floor validation
  against `proxy_admission_wait_timeout_seconds + 2 x
  token_refresh_timeout_seconds` protects the cross-replica single-use
  refresh-token invariant and references settings that remain
  configurable.

Phase 2 (15 removed):

- Scheduler cadences (4): quota planner tick 300 s (the old
  `max(60, ...)` clamp became moot and was dropped), automations poll
  30 s, model-registry refresh 300 s, sticky-session cleanup 300 s —
  constants next to their scheduler builders; every `*_ENABLED` switch
  remains.
- Codex client fingerprint (3): OS `Mac OS 26.5.0`, arch `arm64`,
  terminal `iTerm.app/3.6.10` — `_FINGERPRINT_*` constants in
  `app/core/clients/proxy.py`, maintained in lockstep with
  `CODEX_LB_MODEL_REGISTRY_CLIENT_VERSION` bumps (which stays a setting:
  it doubles as the degraded-startup catalog floor).
- Live-usage write coalescing (2): min interval 5.0 s, queue size 512 —
  constants in `app/modules/usage/live_ingest.py`.
- Request-log count-cache TTL (1): fixed 30.0 s in
  `app/modules/request_logs/repository.py` (the test suite patches the
  constant to 0 where exact totals matter).
- Circuit-breaker tuning (2): failure threshold 5, recovery timeout 60 s
  — constants in `app/core/resilience/circuit_breaker.py`. The Helm chart
  values `config.circuitBreakerFailureThreshold`,
  `config.circuitBreakerRecoveryTimeoutSeconds`, and
  `config.stickySessionCleanupIntervalSeconds` were removed in the same
  change so a default install does not trip its own removal warning.
- Memory warning threshold (1): derived as 80% of
  `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB` in
  `app/core/resilience/memory_monitor.py`. The warning has no meaning on
  its own — it exists to announce that the reject threshold is being
  approached. The only lost configuration is a warning-only setup with no
  reject threshold, an observability half-measure the log stream covers
  anyway. `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB` stays: it is the one
  genuine deployment decision (it depends on host memory size), default 0
  = fully off.
- Images internals (2): host model fixed to `gpt-5.5`
  (`_IMAGES_HOST_MODEL` in `app/modules/proxy/api.py`; the model registry
  has no "default Responses model" concept, so a documented constant
  tracking the bootstrap catalog beats inventing registry plumbing —
  never echoed to clients) and partial-images cap fixed to 3 in
  `app/core/openai/images.py` (an upstream streaming contract).
  `CODEX_LB_IMAGES_DEFAULT_MODEL` stays: the public API contract for
  clients that omit `model`.

Phase 3 (10 removed):

- DB pool tuning (4): background pool size / max overflow always derive
  from `database_pool_size` / `database_max_overflow` (nothing ever set
  the overrides; unconditional derivation also collapses the `background`
  branch out of the engine-kwargs helper so pre-ping/recycle regressions
  like #672 cannot diverge between the two engines); pool checkout
  timeout fixed 30.0 s and recycle window fixed 1800 s
  (`_POSTGRES_POOL_*` constants in `app/db/session.py`).
  `CODEX_LB_DATABASE_POOL_SIZE` / `CODEX_LB_DATABASE_MAX_OVERFLOW` stay:
  PostgreSQL HA operators must budget
  `(pool_size + max_overflow) x replicas <= max_connections`, and the
  Helm chart pins both.
- Soft-drain/probe thresholds (6): drain at 85%/90%, error window 60 s /
  count 2, probe quiet 60 s, success streak 3. They encode the
  deterministic-failover design and interlock — raising one without the
  others degrades failover in non-obvious ways — and
  `app/core/balancer/logic.py` already declared identical constants as
  `evaluate_health_tier` parameter defaults, so the settings were a second
  source of truth for numbers that must not drift. The function keeps its
  full parameter surface for tests; production call sites rely on the
  constant defaults. `CODEX_LB_SOFT_DRAIN_ENABLED` and
  `CODEX_LB_DETERMINISTIC_FAILOVER_ENABLED` stay as the subsystem
  switches.

Phase 4 (3 removed; prewarm canary scaffolding):

- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT`
  and the `..._ALLOW_API_KEY_IDS` / `..._DENY_API_KEY_IDS` cohort lists
  (plus their validator). The canary machinery was one-time rollout
  instrumentation for a finished experiment, not an operator contract.
  Production was verified live on 2026-07-15 before removal: every
  replica ran `prewarm_enabled=False`, percent unset (`None`), empty
  allow/deny lists — and the `canary_percent=None` code path (treat all
  eligible requests, `legacy_all`) is exactly the new unconditional
  behavior, so nothing changed for defaults or production.
  `..._PREWARM_ENABLED` stays (default off, mid-rollout): enabling it is
  a real operator decision; only the scoping machinery went away.
  `prewarm_status=canary_miss` is unreachable and removed from the
  observability contract; see
  `openspec/specs/proxy-runtime-observability/context.md`.
  If a future feature needs percentage or cohort-scoped rollout, that is
  a new OpenSpec change with its own design — re-introducing these
  settings verbatim is explicitly not the path.

## Deprecation policy for removed settings

`extra="ignore"` on `Settings` makes removed env vars inert the moment the
fields are deleted; the startup WARN (`warn_removed_settings()` in
`app/core/config/settings.py`, called from the `app/main.py` lifespan) is
one release of courtesy so operators notice stale configuration. The
warning lists names only, never values, and is removed together with its
`_REMOVED_SETTINGS` entries in a later release (see the next-release
queue above).

## Example

An operator running `CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD=true` upgrades:
startup logs

```
removed setting(s) ignored: CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD — values are now fixed; see PRINCIPLES.md P2 / issue #1340
```

and the equivalent incident-debugging behavior is re-enabled interactively
with `CODEX_LB_TRACE=upstream_payload`. Startup never fails because of a
removed setting, and the fixed built-in value is used.
