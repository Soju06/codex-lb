# admin-auth Context

## Bootstrap Token

### Purpose

The bootstrap token secures the initial remote password setup flow. Without it, anyone on the network could set the dashboard password on a fresh install. It is a one-time credential — only needed during the first `POST /api/dashboard-auth/password/setup` from a non-local client.

### Behavior

**Auto-generation (default path):**

On server startup, if no dashboard password is configured and no `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var is set, the system generates a cryptographically random token (`secrets.token_urlsafe(32)`, 256 bits entropy), stores it in the shared `dashboard_settings` row, and prints it to server logs with visual delimiters.

**Priority chain:**

`get_active_bootstrap_token()` resolves the token using: manual env var → shared DB-backed token → None. A single accessor function is the sole source of truth, used by both the session endpoint and the password setup endpoint.

**Lifecycle:**

1. Server starts → `ensure_auto_bootstrap_token()` checks conditions → generates and persists the token if needed → logs it
2. User copies token from `docker logs` → enters it in the dashboard with new password
3. `setup_password()` validates token → atomically sets the password → clears the shared stored token
4. Token is cleared from shared storage. Subsequent requests don't need it.

**Restart behavior:**

If the server restarts before a password is set, the same stored token remains valid and any replica can validate it. Replicas may log the same token again on startup for discoverability. If an authenticated admin removes the dashboard password later, a new bootstrap token is generated immediately so remote setup continues to work without restart.

### Manual Override

Set `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=<value>` as an environment variable before starting. When set:
- Auto-generation is skipped
- No token is logged
- The env var value is used for validation
- The token persists across restarts (it's in the environment)

### Localhost Bypass

Requests from localhost (127.0.0.1, ::1) bypass bootstrap entirely — no token or password needed for initial setup. This is handled by `is_local_request()` in `app/core/request_locality.py` and checked in both the session endpoint and the auth guard.

### Threat Model

- **Token in logs**: Acceptable risk (same pattern as Grafana/GitLab/Portainer). `docker logs` requires container access. Token is one-time — useless after password is set.
- **Token in shared DB storage**: Shared across replicas and cleared as soon as password setup succeeds.
- **Replica safety**: Any pod can validate the same bootstrap token, avoiding load-balancer flakiness during first-run setup.

## Session Management

Stateless encrypted cookies using Fernet. Session payload: `{exp, pw, tv}`. TTL: 12 hours. No server-side session storage.

## Rate Limiting

Password login and TOTP verification: max 8 attempts per 60-second window per client IP. Stored in `rate_limit_attempts` table. Returns 429 with `Retry-After` header.

## Audit Logging

Events: `login_success`, `login_failed`, `totp_enabled`, `totp_disabled`, `settings_changed`. Stored in `audit_logs` table via `AuditService.log_async()`.
