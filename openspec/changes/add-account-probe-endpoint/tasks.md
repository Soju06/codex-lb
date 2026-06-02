## 1. Schemas

- [x] 1.1 Add `AccountProbeRequest` and `AccountProbeResponse` to `app/modules/accounts/schemas.py`. `AccountProbeResponse` carries `status`, `account_id`, `probe_status_code`, `primary_used_percent_before`/`after`, `secondary_used_percent_before`/`after`, `account_status_before`/`after`.

## 2. Service

- [x] 2.1 Add `AccountNotProbableError(Exception)` to `app/modules/accounts/service.py`.
- [x] 2.2 Add `AccountsService.probe_account(account_id: str, model: str | None = None) -> AccountProbeResponse | None` that:
  - returns `None` when the account is missing,
  - raises `AccountNotProbableError` when `status in (PAUSED, DEACTIVATED)`,
  - captures `primary` and `secondary` usage snapshots via `self._usage_repo.latest_entry_for_account`,
  - refreshes stale account token material before decrypting and sending the probe,
  - decrypts the access token via `self._encryptor.decrypt`,
  - calls a new private `_send_probe_request(*, access_token, chatgpt_account_id, model) -> int` helper using the shared leased HTTP session, a 30s total timeout, a 10s `sock_connect` timeout, the upstream HTTP status on response, and `0` on network failure,
  - triggers `self._usage_updater.force_refresh(account)` after the probe so the post-probe `/wham/usage` fetch bypasses freshness/cooldown gates while respecting the refresh kill switch,
  - reloads account + usage and returns the before/after snapshot.
- [x] 2.3 Confirm the probe never logs the decrypted access token.

## 3. API route

- [x] 3.1 Add `POST /api/accounts/{account_id}/probe` to `app/modules/accounts/api.py` (inherits the existing router's `validate_dashboard_session` / `set_dashboard_error_format` dependencies).
- [x] 3.2 Body model: `AccountProbeRequest | None`; default model `"gpt-5.5"` when omitted.
- [x] 3.3 On `None` result raise `DashboardNotFoundError("Account not found", code="account_not_found")`.
- [x] 3.4 On `AccountNotProbableError` raise `DashboardConflictError(str(exc), code="account_not_probable")`.
- [x] 3.5 Log `AuditService.log_async("account_probed", actor_ip=..., details={"account_id": ..., "probe_status_code": ..., "model": ...})` after a successful probe.

## 4. Tests

- [x] 4.1 `tests/unit/test_accounts_service_probe.py` covers:
  - `test_probe_account_returns_none_for_missing_account`
  - `test_probe_account_rejects_paused_account`
  - `test_probe_account_rejects_deactivated_account`
  - `test_probe_account_captures_before_after_snapshot`
  - `test_probe_account_uses_default_model_when_omitted`
  - `test_probe_account_refreshes_stale_token_before_upstream_probe`
  - `test_probe_account_does_not_send_probe_when_token_refresh_fails`
  - `test_probe_account_never_logs_access_token`
  - `test_probe_account_surfaces_network_failure_status`
  - `test_send_probe_request_uses_shared_http_client`
- [x] 4.2 `tests/integration/test_accounts_api_probe.py` covers:
  - `test_probe_missing_account_returns_404`
  - `test_probe_paused_account_returns_409`
  - `test_probe_active_account_returns_snapshot`
  - `test_probe_uses_default_model_when_body_omitted`
- [x] 4.3 `tests/unit/test_usage_updater.py` covers forced-refresh freshness bypass, singleflight serialization, cancellation, auth-cooldown bypass, and refresh-disabled behavior.

## 5. Spec + validation

- [x] 5.1 Add a new requirement under `usage-refresh-policy` (delta at `openspec/changes/add-account-probe-endpoint/specs/usage-refresh-policy/spec.md`) covering operator-triggered probe + post-probe forced usage refresh.
- [x] 5.2 Run `uv run pytest tests/unit/test_accounts_service_probe.py tests/integration/test_accounts_api_probe.py tests/unit/test_usage_updater.py -q` and confirm clean.
- [x] 5.3 Run `uv run pytest tests/unit/test_load_balancer.py tests/integration/test_accounts_api.py -q` and confirm no regression.
- [x] 5.4 Run `uv run ruff check app/modules/accounts app/modules/usage tests/unit/test_accounts_service_probe.py tests/integration/test_accounts_api_probe.py tests/unit/test_usage_updater.py` and confirm clean.
- [x] 5.5 Run `uv run openspec validate add-account-probe-endpoint --strict`, `uv run openspec change show add-account-probe-endpoint --json --deltas-only`, and `uv run openspec validate --specs` and confirm clean.
