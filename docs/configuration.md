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

## Everything else

The remaining settings (timeouts, connection pools, bulkheads, session bridge, leader election, observability, circuit breakers, ...) are advanced operational tunables with tested defaults. The full generated [settings reference](reference/settings.md) lists every variable with its type and default. Do not tune them unless the documentation for your specific scenario says so:

- [Deployment on Kubernetes / multi-replica](deployment/kubernetes.md)
- [Remote access and reverse proxies](deployment/remote.md)
- [Database backends](database.md)
- [Troubleshooting](troubleshooting.md)

Runtime behavior such as the routing strategy, upstream stream transport, and per-account limits is configured live in the dashboard under **Settings** — no restart required. The resilience toggles (soft drain, deterministic failover, circuit breaker) live there too, under **Settings → Advanced → Resilience**; their `CODEX_LB_*` variables remain a deprecated fallback for one release while the dashboard value is unset.

## Where settings live

The dashboard is the primary configuration surface. Environment variables are for two things only: values needed before the database is reachable (data directory, database URL, encryption key, port) and values that legitimately differ between replicas (advertise URLs, trusted-proxy CIDRs, bind hosts). Everything an operator might change while the proxy is running — caps, timeouts, retries, feature toggles, retention — belongs in the dashboard and takes effect on every replica without a restart. Where a dashboard setting also has an environment fallback, precedence is fixed: code default, then environment, then a value set in the dashboard; the environment never overrides a value you set in the dashboard, and clearing the dashboard value returns to the environment (or default).

This is the placement rule for new and migrated settings; not every existing setting follows it yet. Some tunables are still environment-only, and two variables still override or gate a dashboard value (`CODEX_LB_TELEMETRY_ENABLED` overrides the persisted telemetry consent; `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED=false` blocks enabling automatic reset-credit redemption). The [settings reference](reference/settings.md) lists what is currently read from the environment; the remaining moves are tracked in the `codify-configuration-tiers` change. The rule, the tier of each setting, and the deprecation path for environment variables are specified in [configuration-tiers](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/configuration-tiers) (delta under `openspec/changes/codify-configuration-tiers/` until archived).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations) · [configuration-tiers](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/configuration-tiers)*
