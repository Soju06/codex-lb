# Authentication

This page covers **dashboard** authentication. For protecting the proxy routes that clients call, see [API Keys](api-keys.md).

## Dashboard authentication modes

`codex-lb` supports three dashboard auth modes via environment variables:

- `CODEX_LB_DASHBOARD_AUTH_MODE=standard` — built-in dashboard password with optional TOTP from the Settings page. This is the default.
- `CODEX_LB_DASHBOARD_AUTH_MODE=trusted_header` — trust a reverse-proxy auth header such as Authelia's `Remote-User`, but only from `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS`. Built-in password/TOTP remain available as an optional fallback, and password/TOTP management still requires a fallback password session.
- `CODEX_LB_DASHBOARD_AUTH_MODE=disabled` — fully bypass dashboard auth. Use only behind network restrictions or external auth. Built-in password/TOTP management is disabled in this mode.

`trusted_header` mode also requires:

```bash
CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true
CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16
CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User
```

If the trusted header is missing and no fallback password is configured, the dashboard fails closed and shows a reverse-proxy-required message instead of loading the UI.

Ready-to-run Docker commands for both non-default modes are in [Docker deployment — auth mode examples](deployment/docker.md#auth-mode-examples). For Helm, pass the same values through `extraEnv`.

## Roles and permissions

The dashboard has two built-in roles. **Admin** (the password, trusted-header, disabled-auth, or local-bootstrap principal) holds every permission. **Guest** (the optional read-only role) holds only `dashboard:read` and `accounts:read`: it can read dashboard overview and usage data, reports, redacted request logs, and account status. It cannot read conversations, conversation archives, the audit log, the API-key inventory, egress-proxy or sticky-session configuration, cannot export account credentials, and cannot change state. Account e-mails are masked for guests (`a***@example.com`) and upstream account identifiers are hidden. Changing or removing the guest password, turning guest access off, or calling `POST /api/dashboard-auth/guest/logout-all` logs every guest out immediately.

Internally, authorization is expressed as fine-grained permissions such as `accounts:export`, `security:write`, `conversations:read`, and `audit:read`, each granted with an `all` or `own` scope. The session API still reports only the coarse `read` / `write` values. When a request lacks a specific permission the API answers `403` with error code `permission_required` and names the missing permission in `param`:

```json
{"error": {"code": "permission_required", "message": "Dashboard permission 'accounts:export' is required", "param": "accounts:export"}}
```

Mutations gated only by the generic write check keep returning `read_only_access`; routes gated by a specific permission (account credential export, security settings, firewall rules, guest password, upstream-proxy endpoint creation) return `permission_required` instead. The full permission list, the built-in grant table, and the per-route requirements are normative in the [admin-auth spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth).

## First-time remote access

Setting the initial dashboard password from a remote machine requires a one-time bootstrap token — see [Getting Started](getting-started.md#remote-setup-bootstrap-token).

---

*Specs: [admin-auth](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth) · [api-firewall](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-firewall)*
