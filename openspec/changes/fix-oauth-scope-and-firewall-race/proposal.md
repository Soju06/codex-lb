## Why

Two dashboard API regressions can produce incorrect runtime behavior:

- OAuth start may use a random scope key when no scope cookie is present, while status/complete use IP+UA fallback, causing stuck `pending` status.
- Firewall allowlist creation uses a check-then-insert sequence that can return 500 under concurrent duplicate requests.

## What Changes

- Derive `/api/oauth/start` scope key from the same request fallback path used by `/api/oauth/status` and `/api/oauth/complete` when scope cookie is absent.
- Handle duplicate firewall allowlist inserts atomically at repository write time and map duplicate insert races to `ip_exists`.
- Add regression tests for cookie-less OAuth device flow and repository conflict mapping.

## Capabilities

### Updated Capabilities
- `dashboard-auth`: OAuth flow state scope consistency across start/status/complete requests.
- `api-firewall`: Duplicate firewall IP writes under concurrent requests return domain conflict errors.

## Impact

- Code: `app/modules/oauth/api.py`, `app/modules/firewall/repository.py`, `app/modules/firewall/service.py`
- Tests: `tests/integration/test_oauth_flow.py`, `tests/unit/test_firewall_service.py`
