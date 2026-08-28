# Change: reduce-settings-surface-phase-1

## Why

Issue #1340 (simplicity backlog: settings-surface reduction) and
PRINCIPLES.md P2 ("a setting the operator never needs to touch is a default
in disguise"): the `Settings` class carried 165 env-settable fields, of which
a substantial share were internals that no supported deployment should ever
change — OAuth protocol identity values that break login when altered,
background-scheduler cadence numbers, incident-only debug log switches,
redundant per-class concurrency overrides, and cross-replica claim polling
intervals. Every such field is documentation debt, review surface, and an
invitation to misconfigure. None of the removed fields is referenced by
`openspec/specs/**`, `.env.example`, `docs/`, or the Helm chart.

## What Changes

Phase 1 removes the zero-risk batch: 24 fields deleted, 1 added
(165 -> 142 fields), with no behavior change for default installs.

- **OAuth protocol constants (6 removed)**: `auth_base_url`,
  `oauth_client_id`, `oauth_originator`, `oauth_scope`, `oauth_redirect_uri`,
  `oauth_callback_port` become module constants in
  `app/core/config/settings.py`. They identify codex-lb to OpenAI; changing
  any of them breaks login. `oauth_timeout_seconds` and `oauth_callback_host`
  remain settings.
- **Auth guardian tuning (7 removed)**: interval, max refresh age, batch
  size, concurrency, jitter, and failure backoff base/max become constants in
  `app/core/auth/guardian.py` at their previous defaults.
  `auth_guardian_enabled` remains the single switch.
- **Debug log booleans (6 removed, 1 added)**: the six `log_proxy_*` /
  `log_upstream_*` booleans are replaced by one `CODEX_LB_TRACE` setting, a
  comma-separated list of trace channels (`shape`, `shape_raw_cache_key`,
  `payload`, `service_tier`, `upstream_summary`, `upstream_payload`). Empty
  (the default) keeps everything off, matching the previous defaults.
  Why not a default: this is an incident-debugging knob for interactive use
  only; there is no correct steady-state value other than "off".
- **Bulkhead per-class overrides (3 removed)**:
  `bulkhead_proxy_http_limit`, `bulkhead_proxy_websocket_limit`,
  `bulkhead_proxy_compact_limit` are always derived from
  `bulkhead_proxy_limit` exactly as the removed defaulting validator did
  (http = websocket = proxy limit; compact = min(http, 16), 0 when http is 0).
- **Token-refresh claim wait/poll (2 removed)**:
  `token_refresh_claim_wait_seconds` (8.0) and
  `token_refresh_claim_poll_seconds` (0.25) become constants in
  `app/modules/accounts/auth_manager.py`. `token_refresh_claim_ttl_seconds`
  and its floor validation (admission wait + 2x refresh timeout) are
  unchanged.
- **One-release removal warning**: startup logs a single WARN listing any
  removed `CODEX_LB_*` env names still present in the environment
  (`extra="ignore"` already makes them inert).

## Impact

- Affected specs: `deployment-installation` (new requirement: removed
  tunables are fixed and warn for one release)
- Affected code: `app/core/config/settings.py`, `app/core/clients/oauth.py`,
  `app/core/clients/proxy.py`, `app/core/auth/refresh.py`,
  `app/core/auth/guardian.py`, `app/modules/oauth/service.py`,
  `app/modules/accounts/auth_manager.py`,
  `app/modules/proxy/_service/observability.py`, `app/main.py`
- Operator impact: none for default installs. Deployments that set a removed
  env var keep working on the fixed default and see one startup WARN.
- Not in scope: further settings-surface phases tracked in #1340 (the issue
  stays open).
