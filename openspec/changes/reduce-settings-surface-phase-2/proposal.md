# Change: reduce-settings-surface-phase-2

## Why

Phase 2 of issue #1340 (simplicity backlog: settings-surface reduction) and
PRINCIPLES.md P2 ("a setting the operator never needs to touch is a default
in disguise"). After phase 1 (`reduce-settings-surface-phase-1`) the
`Settings` class still carried 142 env-settable fields, of which another
batch are internals no supported deployment should change: background
scheduler cadences, the Codex client fingerprint triplet, live-usage write
coalescing, the request-log count-cache TTL, circuit-breaker tuning, the
memory warning threshold, and images-route internals.

## What Changes

Phase 2 removes 15 more fields (142 -> 127), with no behavior change for
default installs.

- **Scheduler cadences (4 removed)**: `quota_planner_tick_seconds` (fixed
  300, making the old `max(60, ...)` clamp moot),
  `automations_scheduler_interval_seconds` (fixed 30),
  `model_registry_refresh_interval_seconds` (fixed 300), and
  `sticky_session_cleanup_interval_seconds` (fixed 300) become constants
  next to their scheduler builders. Each scheduler's `*_enabled` switch
  remains.
- **Codex fingerprint (3 removed)**: `codex_fingerprint_os`,
  `codex_fingerprint_arch`, `codex_fingerprint_terminal` become the fixed
  `_FINGERPRINT_*` constants in `app/core/clients/proxy.py` (previous
  defaults `Mac OS 26.5.0` / `arm64` / `iTerm.app/3.6.10`). They are
  maintained in lockstep with `model_registry_client_version` bumps, which
  stays a setting because it doubles as the degraded-startup catalog floor.
- **Live-usage write coalescing (2 removed)**:
  `live_usage_write_min_interval_seconds` (fixed 5.0) and
  `live_usage_queue_size` (fixed 512) become constants in
  `app/modules/usage/live_ingest.py`. `live_usage_ingestion_enabled`
  remains the single switch.
- **Request-log count cache (1 removed)**:
  `request_log_count_cache_ttl_seconds` becomes a fixed 30.0-second TTL in
  `app/modules/request_logs/repository.py`; the test suite patches the
  constant to 0 where exact totals matter.
- **Circuit breaker tuning (2 removed)**:
  `circuit_breaker_failure_threshold` (fixed 5) and
  `circuit_breaker_recovery_timeout_seconds` (fixed 60) become constants in
  `app/core/resilience/circuit_breaker.py`. `circuit_breaker_enabled`
  remains the single switch. The Helm chart values
  `config.circuitBreakerFailureThreshold` and
  `config.circuitBreakerRecoveryTimeoutSeconds` (and
  `config.stickySessionCleanupIntervalSeconds`) are removed with it.
- **Memory warning threshold (1 removed)**: `memory_warning_threshold_mb`
  is now derived as 80% of `memory_reject_threshold_mb` in
  `app/core/resilience/memory_monitor.py`; the warning exists only as an
  early signal that the reject threshold is being approached, so it is not
  an independent operator decision. Both default to off (0), so default
  behavior is unchanged. `memory_reject_threshold_mb` stays.
- **Images internals (2 removed)**: `images_host_model` becomes the fixed
  internal host model constant in `app/modules/proxy/api.py` (`gpt-5.5`,
  tracking the registry bootstrap catalog; never echoed to clients), and
  `images_max_partial_images` becomes a fixed cap of 3 in
  `app/core/openai/images.py` (an upstream streaming contract, previously
  already clamped to 0..3). `images_default_model` stays: it is the public
  model contract for clients that omit `model`.
- **One-release removal warning**: the phase-2 env names join
  `_REMOVED_SETTINGS`, so startup logs the existing single WARN when any
  of them are still set (`extra="ignore"` already makes them inert).

## Impact

- Affected specs: `deployment-installation` (new requirement covering the
  phase-2 fixed values and derived memory warning); non-normative wording
  update in `openspec/specs/quota-phase-planner/context.md`.
- Affected code: `app/core/config/settings.py`,
  `app/modules/quota_planner/scheduler.py`,
  `app/modules/automations/scheduler.py`,
  `app/core/openai/model_refresh_scheduler.py`,
  `app/modules/sticky_sessions/cleanup_scheduler.py`,
  `app/core/clients/proxy.py`, `app/modules/usage/live_ingest.py`,
  `app/modules/request_logs/repository.py`,
  `app/core/resilience/circuit_breaker.py`,
  `app/core/resilience/memory_monitor.py`, `app/main.py`,
  `app/modules/proxy/api.py`, `app/modules/proxy/images_service.py`,
  `app/core/openai/images.py`, `deploy/helm/codex-lb/**`
- Operator impact: none for default installs. Deployments that set a
  removed env var keep working on the fixed value and see one startup WARN.
  Helm installs that overrode the three removed chart values fall back to
  the identical fixed constants.
- Not in scope: further settings-surface phases tracked in #1340 (the issue
  stays open).
