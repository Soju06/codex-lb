# Configuration

codex-lb runs with zero configuration — every setting has a working default, and container vs. host environments are auto-detected. Configure only what a docs page for your scenario tells you to.

Settings are environment variables with the `CODEX_LB_` prefix, or a `.env.local` file next to the process. The commented sample lives at [`.env.example`](https://github.com/Soju06/codex-lb/blob/main/.env.example).

## The settings that matter

| Variable | Default | When to set it |
|----------|---------|----------------|
| `CODEX_LB_DATA_DIR` | `~/.codex-lb` (host) / `/var/lib/codex-lb` (Docker) | Move the data directory (DB, encryption key, archives) |
| `PORT` | `2455` | Change the listen port on host (uvx/local) runs — process environment only, not `.env.local` (env files map only `CODEX_LB_`-prefixed variables). In Docker the container always listens on 2455 (the entrypoint pins `--port 2455`); change the host side of the compose `ports` mapping instead (e.g. `"8080:2455"`) |
| `CODEX_LB_DATABASE_URL` | SQLite in the data dir | Use PostgreSQL — see [Database](database.md) |
| `CODEX_LB_ENCRYPTION_KEY_FILE` | auto-generated in the data dir | Pin the key location (recommended for Docker volumes and required to be shared across replicas) |
| `CODEX_LB_DASHBOARD_AUTH_MODE` | `standard` | `trusted_header` / `disabled` — see [Authentication](authentication.md) |
| `CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS` | `false` | Behind a reverse proxy — see [Remote Access](deployment/remote.md) |
| `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS` | `127.0.0.1/32,::1/128` | CIDRs allowed to set `X-Forwarded-For` |
| `CODEX_LB_OAUTH_CALLBACK_HOST` | auto-detected (`0.0.0.0` in containers) | Rarely — bind the OAuth login callback explicitly |

## Dashboard language

Use the language button in the dashboard header, or open the menu on mobile,
to choose English, 简体中文, 한국어, or 日本語. The dashboard detects Japanese
browser settings such as `ja-JP` on the first visit and remembers your selection
in that browser. To open a Japanese view directly, append `?lang=ja` to a
dashboard URL (or `&lang=ja` when it already has query parameters).

Japanese applies to navigation, settings, dialogs, and the API-key expiry
calendar. Date and time labels follow the selected language; explicit date/time
preferences in **Settings → Appearance** still apply. Compact quantities keep
`K/M/B` suffixes and USD amounts keep `$` across languages.

## Everything else

The remaining settings (timeouts, connection pools, bulkheads, session bridge, leader election, observability, circuit breakers, ...) are advanced operational tunables with tested defaults. The full generated [settings reference](reference/settings.md) lists every variable with its type and default. Do not tune them unless the documentation for your specific scenario says so:

- [Deployment on Kubernetes / multi-replica](deployment/kubernetes.md)
- [Remote access and reverse proxies](deployment/remote.md)
- [Database backends](database.md)
- [Troubleshooting](troubleshooting.md)

Runtime behavior such as the routing strategy, upstream stream transport, and per-account limits is configured live in the dashboard under **Settings** — no restart required.

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations) · [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture)*
