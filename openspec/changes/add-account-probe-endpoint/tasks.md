## 1. Schemas

- [x] 1.1 Add `AccountProbeRequest` and `AccountProbeResponse` to `app/modules/accounts/schemas.py`. `AccountProbeResponse` carries `status`, `account_id`, `probe_status_code`, `primary_used_percent_before`/`after`, `secondary_used_percent_before`/`after`, `account_status_before`/`after`.

## 2. Service

- [x] 2.1 Add `AccountNotProbableError(Exception)` to `app/modules/accounts/service.py`.
- [x] 2.2 Add `AccountsService.probe_account(account_id: str, model: str = "gpt-5.5") -> AccountProbeResponse | None` that:
  - returns `None` when the account is missing,
  - raises `AccountNotProbableError` when `status in (PAUSED, DEACTIVATED)`,
  - captures `primary` and `secondary` usage snapshots via `self._usage_repo.latest_by_account`,
  - decrypts the access token via `self._encryptor.decrypt`,
  - calls a new private `_send_probe_request(*, access_token, chatgpt_account_id, model) -> int` helper (aiohttp, 30s total timeout, returns the upstream HTTP status, returns `0` on network failure),
  - triggers `self._usage_updater.refresh_accounts([account], latest_usage)` after the probe,
  - reloads account + usage and returns the before/after snapshot.
- [x] 2.3 Confirm the probe never logs the decrypted access token.

## 3. API route

- [x] 3.1 Add `POST /api/accounts/{account_id}/probe` to `app/modules/accounts/api.py` (inherits the existing router's `validate_dashboard_session` / `set_dashboard_error_format` dependencies).
- [x] 3.2 Body model: `AccountProbeRequest | None`; default model `"gpt-5.5"` when omitted.
- [x] 3.3 On `None` result raise `DashboardNotFoundError("Account not found", code="account_not_found")`.
- [x] 3.4 On `AccountNotProbableError` raise `DashboardConflictError(str(exc), code="account_not_probable")`.
- [x] 3.5 Log `AuditService.log_async("account_probed", actor_ip=..., details={"account_id": ..., "probe_status_code": ..., "model": ...})` after a successful probe.

## 4. Tests

- [x] 4.1 `tests/unit/test_accounts_service_probe.py`:
  - `test_probe_account_returns_none_for_missing_account`
  - `test_probe_account_rejects_paused_account`
  - `test_probe_account_rejects_deactivated_account`
  - `test_probe_account_captures_before_after_snapshot` (mocks `_send_probe_request` and the usage refresh path)
  - `test_probe_account_never_logs_access_token` (captures caplog, asserts the decrypted token does not appear)
- [x] 4.2 `tests/integration/test_accounts_api_probe.py`:
  - `test_probe_endpoint_returns_404_for_missing_account`
  - `test_probe_endpoint_returns_409_for_paused_account`
  - `test_probe_endpoint_returns_200_and_persists_audit_entry` (mock `_send_probe_request` so no real upstream call)

## 5. Spec + validation

- [x] 5.1 Add a new requirement under `usage-refresh-policy` (delta at `openspec/changes/add-account-probe-endpoint/specs/usage-refresh-policy/spec.md`) covering operator-triggered probe + post-probe refresh.
- [x] 5.2 Run `uv run pytest tests/unit/test_accounts_service_probe.py tests/integration/test_accounts_api_probe.py -q` and confirm clean.
- [x] 5.3 Run `uv run pytest tests/unit/test_load_balancer.py tests/integration/test_accounts_api.py -q` and confirm no regression.
- [x] 5.4 Run `uv run ruff check app/modules/accounts tests/unit/test_accounts_service_probe.py tests/integration/test_accounts_api_probe.py` and confirm clean.
