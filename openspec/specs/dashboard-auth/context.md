# Dashboard Auth Context

## Purpose and scope

This capability documents how the dashboard browser client passes the setup token required by TOTP setup endpoints.

## Decision

The backend already enforces `X-Codex-LB-Setup-Token` for setup routes. The dashboard UI requests setup token only when setup start/confirm returns `dashboard_setup_forbidden`, then retries the request with the header.

## Constraints and failure modes

- If `CODEX_LB_DASHBOARD_SETUP_TOKEN` is unset, setup is disabled server-side.
- If the dashboard input token does not match server configuration, setup requests fail with `dashboard_setup_forbidden`.

## Example

1. Operator sets `CODEX_LB_DASHBOARD_SETUP_TOKEN` in `.env` and restarts the app.
2. Operator clicks `Setup TOTP`; backend returns `dashboard_setup_forbidden`.
3. Dashboard prompts for setup token, operator enters it, and setup request is retried successfully.
