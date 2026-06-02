## Why

After OpenAI-side "team reset events" the upstream rate-limiter at `/wham/usage` can keep returning the pre-reset `used_percent` for an account even though the Settings UI and the actual usage have reset (see https://github.com/Soju06/codex-lb/issues/676 and https://github.com/Soju06/codex-lb/issues/677). codex-lb faithfully mirrors `/wham/usage`, so the affected account stays in `rate_limited` / `quota_exceeded` until upstream re-evaluates. A real `responses.create` request against the affected account wakes the upstream limiter and the next refresh tick auto-recovers the account, but the balancer excludes the affected account from selection so it cannot probe itself organically. The operator currently has no first-class action besides waiting for the natural window roll or editing the DB directly.

This change adds the per-account "force-probe" admin endpoint requested in upstream issue #677.

## What Changes

- Add `POST /api/accounts/{account_id}/probe` (admin / dashboard auth) returning the probe HTTP status plus before/after `primary_used_percent`, `secondary_used_percent`, and account `status`.
- The endpoint:
  1. Looks up the account; returns 404 if missing; 409 if `paused`/`deactivated` (probing a hard-blocked account is rejected).
  2. Decrypts the account access token and sends a single minimal `responses.create` directly to `{upstream_base_url}/codex/responses` (stream=true, store=false, max_output_tokens=1). The call bypasses load-balancer scoring (it does not touch `LoadBalancer.select_account`) and consumes a tiny amount of upstream quota by design.
  3. Triggers an immediate `UsageUpdater.refresh_accounts([account], ...)` so the post-probe `/wham/usage` snapshot is persisted.
  4. Reloads the account + latest primary/secondary usage and returns the before/after snapshot.
- Add `AccountProbeRequest` (optional `model`, default `gpt-5.5`) and `AccountProbeResponse` schemas in `app/modules/accounts/schemas.py`.
- Add an `AuditService.log_async("account_probed", ...)` entry so probe attempts are visible in the existing audit trail alongside `account_created` / `account_deleted`.
- Add `AccountNotProbableError` (mapped to `DashboardConflictError`) for the paused/deactivated rejection path.
- Cover the new service method with unit tests (`tests/unit/test_accounts_service_probe.py`) and the new route with integration tests (`tests/integration/test_accounts_api_probe.py`).

## Impact

- Operators get a first-class manual recovery action for stuck-rate-limited accounts after OpenAI reset events. Closes the chicken-and-egg trap that #677 describes (LB excludes blocked accounts from selection, so blocked accounts can never wake the upstream limiter via organic traffic).
- Single endpoint, single concern. No changes to selector decision logic, the proxy pipeline, the existing usage refresh cadence, or persistence beyond what `UsageUpdater.refresh_accounts` already writes.
- The probe consumes a tiny amount of upstream quota (one `responses.create` with `max_output_tokens=1`) per invocation. The endpoint requires dashboard auth, so casual misuse is not a concern.
- No frontend / dashboard button in this change. UI button is a follow-up so the API surface can land cleanly with a focused review.
- No change to public client surfaces (`/backend-api/codex/responses`, `/v1/responses`, etc.). The new endpoint lives under the existing dashboard `/api/accounts/*` namespace.
