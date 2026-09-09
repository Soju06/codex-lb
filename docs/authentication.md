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

## Signing in

Dashboard sign-in is per account. The first `POST /api/dashboard-auth/password/setup` (the bootstrap screen) creates the `admin` account; an install upgraded from the shared-password era already has that account with the same password and TOTP secret, so nothing changes for the person signing in. `POST /api/dashboard-auth/password/login` takes `{username?, password}`: on a single-account install the username may be omitted and the login form hides the field (`login.username_field` is `hidden` in the session response); as soon as a second account holds a password the username becomes mandatory and the API answers `422 username_required` without it. Wrong usernames and wrong passwords return the same `401 invalid_credentials` (and take the same time), and failed attempts are limited per client (8 per minute); accounts are never locked out.

Sessions are bound to the account: disabling an account or changing its password ends every one of its sessions on the next request, and `POST /api/dashboard-auth/logout-all` signs the account out of every browser at once (changing the password keeps the browser that changed it signed in). `GET /api/dashboard-auth/me` returns the signed-in account. TOTP secrets belong to the account too; when "require TOTP on login" is enabled, an account that has not enrolled yet can only use the session, TOTP setup/verify, logout, `logout-all`, `me` and password-change routes and gets `403 totp_enrollment_required` everywhere else until it completes `/totp/setup`. Removing the dashboard password (`DELETE /api/dashboard-auth/password`) is only possible while the account is the only one on the install and no invite is pending; it makes the install passwordless again exactly as before, including the regenerated bootstrap token.

Upgrading to this release changes the session cookie format, so everyone signs in once more after the upgrade; cookies from the previous release are rejected rather than trusted.

## Accounts and invites

Adding a person is done from the dashboard-users API (`users:manage`, held by Admin). `POST /api/dashboard-users` with a username and a role creates the account in an *invited* state and returns an invite link token exactly once; hand the link to the person. The link is valid for 24 hours and works once: the person opens `GET /api/dashboard-auth/invite/{token}` to see who invited them and for which role, then sets their own password with `POST /api/dashboard-auth/invite/accept` and is signed in. Nobody but the person ever knows the password. Resending an invite (`POST /api/dashboard-users/{id}/invite`) rotates the token and kills the old link; revoking it (`DELETE .../invite`) removes the invited account again, and expired invited accounts are cleaned up the next time the pending-invite list is read, so a personal install that invited someone and never followed up returns to its single-account state by itself.

Who can assign which role follows one rule: you can only grant (or act on) a role whose permissions are a subset of your own. In practice only an Admin can create another Admin, and a custom role holding `users:manage` can invite Viewers but not Admins or Operators (`403 insufficient_delegation`). Accounts cannot change their own role or disable themselves, at least one active Admin must always remain (`409 last_admin_protected`, enforced inside the write so two admins disabling each other at once leaves exactly one), and in this release the migrated `admin` account keeps its role and status and cannot be deleted (`409 compat_user_locked`; its display name and e-mail can still be edited, and its TOTP can only be reset while "require TOTP on login" is off). Managing accounts requires being signed in as an account: a passwordless local install answers `409 admin_account_required` until a password is set.

Disabling an account (`PATCH /api/dashboard-users/{id}` with `status: disabled`) ends its sessions and turns off every API key it owns, recording `owner_disabled` as the reason; re-enabling the account does not turn the keys back on — `POST /api/dashboard-users/{id}/reactivate-keys` restores exactly those keys and leaves manually deactivated ones alone (revoking a key by hand always wins, and a key whose owner is disabled cannot be re-enabled from the API-key page). Deleting an account keeps its keys, inactive and without an owner. Admins can also reset a lost TOTP secret (`reset-totp`) or sign an account out everywhere (`revoke-sessions`); everyone can edit their own display name and e-mail through `PATCH /api/dashboard-auth/me`. `GET /api/dashboard-roles` and `GET /api/dashboard-roles/permissions` describe the built-in roles and the permission vocabulary. The exact rules are normative in the [dashboard-users spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/dashboard-users) and the [dashboard-roles spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/dashboard-roles).

## Roles and permissions

The dashboard has five built-in roles. An account holds one of the assignable presets — **Admin** (everything), **Operator** (accounts, all API keys, operations), **Viewer** (read-only) — and the trusted-header, disabled-auth, and local-bootstrap principals act as Admin without an account. **Member** (own API keys and own usage only) becomes assignable when own-scoped views ship; until then an account holding it can sign in but every dashboard read answers `403 permission_required` (`dashboard:read`). **Guest** (the optional read-only role) is never an account: it holds only `dashboard:read` and `accounts:read`, so it can read dashboard overview and usage data, reports, redacted request logs, and account status. It cannot read conversations, conversation archives, the audit log, the API-key inventory, egress-proxy or sticky-session configuration, cannot export account credentials, and cannot change state. Account e-mails are masked for guests (`a***@example.com`) and upstream account identifiers are hidden. Changing or removing the guest password, turning guest access off, or calling `POST /api/dashboard-auth/guest/logout-all` logs every guest out immediately. In the dashboard, guests see an administrator-only notice on the APIs page and do not see the API key, upstream-proxy, or sticky-session sections in Settings, because those reads are answered with `403` for read-only sessions.

Internally, authorization is expressed as fine-grained permissions such as `accounts:export`, `security:write`, `conversations:read`, and `audit:read`, each granted with an `all` or `own` scope. The session API reports the coarse `read` / `write` values followed by every grant as `<permission>:<scope>` (for example `api_keys:write:all`); the `role` field stays `admin` or `guest`, and the account's real role travels in `user.role`. When a request lacks a specific permission the API answers `403` with error code `permission_required` and names the missing permission in `param`:

```json
{"error": {"code": "permission_required", "message": "Dashboard permission 'accounts:export' is required", "param": "accounts:export"}}
```

Mutations gated only by the generic write check keep returning `read_only_access`; routes gated by a specific permission (account credential export, security settings, firewall rules, guest password, upstream-proxy endpoint creation) return `permission_required` instead. The full permission list, the built-in grant table, and the per-route requirements are normative in the [admin-auth spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth).

## Cross-site request protection

State-changing dashboard requests (`POST`, `PUT`, `PATCH`, `DELETE` under `/api/`) are checked against the browser's `Sec-Fetch-Site` and `Origin` headers before they reach any handler. A request the browser marks as cross-site, or whose `Origin` does not match the dashboard's own scheme, host, and port, is refused with `403` and error code `cross_site_request_rejected`; requests without either header (curl, scripts, the CLI) pass through. Bearer-authenticated routes (`/api/fleet/`, `/api/codex/`, or any request with `Authorization: Bearer`) are exempt. Nothing needs to be configured, but a reverse proxy in front of an `http://` dashboard must forward the browser's original `Host` header including its port so the `Origin` comparison can match: nginx `proxy_set_header Host $http_host;` (not `$host`, which drops the port), Apache `ProxyPreserveHost On`; Traefik and Caddy pass `Host` through by default. The exact rules are normative in the [admin-auth spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth).

## First-time remote access

Setting the initial dashboard password from a remote machine requires a one-time bootstrap token — see [Getting Started](getting-started.md#remote-setup-bootstrap-token).

---

*Specs: [admin-auth](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth) · [dashboard-users](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/dashboard-users) · [dashboard-roles](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/dashboard-roles) · [api-firewall](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-firewall)*
