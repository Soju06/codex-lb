# Change: reduce-settings-surface-phase-4

## Why

Phase 4 of issue #1340 (simplicity backlog: settings-surface reduction) and
PRINCIPLES.md P2. The Codex HTTP-bridge prewarm feature shipped with canary
rollout scaffolding: a deterministic sampling percent plus API-key allow/deny
cohort lists, with per-request bucket/cohort observability. That tooling was
one-time rollout instrumentation, not an operator contract. Production was
verified live on 2026-07-15 before this removal: every replica runs with
`prewarm_enabled=False`, `canary_percent` unset (`None`), and empty allow/deny
lists — no active canary configuration exists to strand.

## What Changes

Phase 4 removes 3 fields (127 -> 124 relative to post-phase-2 main; the
parallel phase-3 change tracks its own count), all canary scaffolding
around the surviving
`http_responses_session_bridge_codex_prewarm_enabled` feature flag (which
stays, default off, mid-rollout):

- **Canary settings (3 removed)**:
  `http_responses_session_bridge_codex_prewarm_canary_percent`,
  `http_responses_session_bridge_codex_prewarm_allow_api_key_ids`, and
  `http_responses_session_bridge_codex_prewarm_deny_api_key_ids` (plus their
  `NoDecode` list validator) are deleted. Prewarm eligibility is now the
  `prewarm_enabled` flag alone — no deterministic sampling, no allow/deny
  cohort. With the percent unset the previous code already treated every
  eligible request (`legacy_all`), so default and current-production behavior
  are unchanged.
- **Observability contract (MODIFIED)**: prewarm outcomes remain observable
  (request-log `prewarm_status` and `prewarm_latency_ms`, plus the
  `codex_lb_http_bridge_prewarm_total` counter), but the canary bucket and
  eligibility-cohort dimensions are removed along with the sampling. The
  counter is labelled by `outcome` only, the request-log columns
  `prewarm_canary_bucket` and `prewarm_eligible_reason` stop being written
  (columns stay one release for rolling-upgrade safety; the drop ships in
  the next release), and `prewarm_status=canary_miss` no longer occurs
  (canary sampling was its only source).
- **One-release removal warning**: the phase-4 env names join
  `_REMOVED_SETTINGS`, so startup logs the existing single WARN when any of
  them are still set (`extra="ignore"` already makes them inert).

## Impact

- Affected specs: `proxy-runtime-observability` (prewarm outcome
  observability requirement loses the canary bucket/cohort dimensions; the
  24-hour TTFT runbook breakdown drops the prewarm bucket/cohort axes) and
  `deployment-installation` (new requirement covering the phase-4 removed
  settings, following the phase-1/2 pattern).
- Affected code: `app/core/config/settings.py`,
  `app/modules/proxy/_service/http_bridge/helpers.py`,
  `app/modules/proxy/_service/http_bridge/request_submit.py`,
  `app/modules/proxy/_service/support.py`,
  `app/modules/proxy/_service/request_log.py`,
  `app/modules/proxy/_service/websocket/mixin.py`,
  `app/modules/proxy/service.py`, `app/core/metrics/prometheus.py`,
  `app/modules/request_logs/repository.py`, `app/db/models.py`, and a new
  The request-log columns are retained (unwritten) this release; a follow-up
  release drops them once no old replicas can be writing.
- Operator impact: none for default installs (prewarm remains off by
  default). Deployments that still set a removed env var keep working —
  values are ignored with one startup WARN. Anyone who had a canary percent
  or allowlist configured (none in production, verified) would see prewarm
  apply to all requests whenever the flag is enabled.
- Not in scope: the `prewarm_enabled` flag itself (kept, mid-rollout);
  prewarm lifecycle/cleanup behavior (pinned normatively in
  `responses-api-compat`, untouched); further settings-surface phases
  tracked in #1340 (the issue stays open).
