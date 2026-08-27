# codex-lb

Load balancer for ChatGPT accounts. Pool multiple accounts, track usage, manage API keys, view everything in a dashboard.

| ![dashboard](screenshots/dashboard.jpg) | ![accounts](screenshots/accounts.jpg) |
|:---:|:---:|

## Features

- **Account pooling** — load balance across multiple ChatGPT accounts
- **Usage tracking** — per-account tokens, cost, 28-day trends
- **API keys** — per-key rate limits by token, cost, window, model
- **Dashboard auth** — password + optional TOTP
- **OpenAI-compatible** — Codex CLI, OpenCode, any OpenAI client
- **Auto model sync** — available models fetched from upstream

## Where to go

- [Getting Started](getting-started.md) — Docker / uvx quick start, remote bootstrap token
- [Client Setup](client-setup.md) — Codex CLI, OpenCode, OpenClaw, Python SDK
- [Configuration](configuration.md) — the few settings that matter
- [Anonymous Telemetry](telemetry.md) — collected fields, consent, disabling, and retention
- [Authentication](authentication.md) — dashboard auth modes
- [Conversations](conversations.md) — dashboard view and conversation APIs
- [API Keys](api-keys.md) — protecting proxy routes
- [Routing](routing.md) — routing strategy guide
- [Database](database.md) — SQLite / PostgreSQL, data paths, Postgres upgrades
- [Deployment](deployment/docker.md) — Docker, [Kubernetes](deployment/kubernetes.md), [remote access](deployment/remote.md)
- [Troubleshooting](troubleshooting.md)

## Screenshots

| Settings | Login |
|:---:|:---:|
| ![settings](screenshots/settings.jpg) | ![login](screenshots/login.jpg) |

| Dashboard (dark) | Accounts (dark) | Settings (dark) |
|:---:|:---:|:---:|
| ![dashboard-dark](screenshots/dashboard-dark.jpg) | ![accounts-dark](screenshots/accounts-dark.jpg) | ![settings-dark](screenshots/settings-dark.jpg) |

## Community companions

These independent projects consume the existing dashboard API and are
maintained outside codex-lb:

- [Codex LB Status Bar](https://github.com/sm1ee/codex-lb-statusbar) — a native
  macOS app with account status, quota details, and authenticated account
  controls.
- [codex-lb SwiftBar](https://github.com/joschi655/codex-lb-swiftbar) — a
  read-only SwiftBar/Bun monitor for account-pool status and quota headroom.

Prefer a guest dashboard session for monitoring-only access when the companion
supports it, and grant admin access only for Status Bar account controls.
codex-lb SwiftBar is read-only; consult its compatibility table for the
authentication modes supported by the current release. Review each project's
repository and release notes before connecting it.

OpenSpec: the [user-documentation capability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/user-documentation)
governs this listing, while [admin-auth](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/admin-auth)
defines the guest and admin access contracts.

---

codex-lb is spec-driven: normative behavior lives in [OpenSpec capabilities](https://github.com/Soju06/codex-lb/tree/main/openspec/specs) in the repository. Docs pages describe how to use the project and link back to the specs that govern them.
