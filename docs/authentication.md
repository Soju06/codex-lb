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

## First-time remote access

Setting the initial dashboard password from a remote machine requires a one-time bootstrap token — see [Getting Started](getting-started.md#remote-setup-bootstrap-token).

## Moving accounts between installations

On **Accounts**, use **Export accounts** to download all accounts or a selected subset as a passphrase-encrypted bundle. The passphrase cannot be recovered; store it separately from the bundle. The bundle contains usable credentials and portable account metadata, so handle it as sensitive even though its contents are encrypted.

On the destination installation, choose **Add account → Import account bundle**, select the file, and enter the passphrase. Review the masked preflight list, then either skip matching accounts or explicitly confirm replacement. The destination encrypts imported credentials with its own at-rest key. Usage history, health/status state, API-key assignments, proxy bindings, global settings, and installation encryption keys are never transferred.

The existing single-account **Import auth.json** and selected-account `auth.json` export remain available as distinct plaintext credential flows. The maximum encrypted upload and decrypted payload size defaults to 8 MiB and can be changed with `CODEX_LB_ACCOUNT_BUNDLE_MAX_BYTES`.

---

*Specs: [admin-auth](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth) · [api-firewall](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-firewall) · [account-bundles](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-bundles)*
