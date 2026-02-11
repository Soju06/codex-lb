## Why

Dashboard TOTP setup fails from the web UI even when `CODEX_LB_DASHBOARD_SETUP_TOKEN` is configured, because the browser client does not send the required setup token header.

## What Changes

- Add dashboard UI support for interactive setup token entry used only for TOTP setup requests.
- Send `X-Codex-LB-Setup-Token` from UI on `/api/dashboard-auth/totp/setup/start` and `/api/dashboard-auth/totp/setup/confirm`.
- Prompt for setup token only when setup requests are rejected with `dashboard_setup_forbidden`, then retry.

## Capabilities

### New Capabilities
- `dashboard-auth`: Dashboard-side TOTP setup and setup token transport requirements.

## Impact

- Code: `app/static/index.html`, `app/static/index.js`
- Tests: Existing integration tests continue to validate backend header enforcement
