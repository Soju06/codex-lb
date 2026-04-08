<!--
About
Codex/ChatGPT account load balancer & proxy with usage tracking, dashboard, and OpenCode-compatible endpoints

Topics
python oauth sqlalchemy dashboard load-balancer openai rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode

Resources
-->

# codex-lb

Load balancer for ChatGPT accounts. Pool multiple accounts, track usage, manage API keys, view everything in a dashboard.

| ![dashboard](docs/screenshots/dashboard.jpg) | ![accounts](docs/screenshots/accounts.jpg) |
|:---:|:---:|

<details>
<summary>More screenshots</summary>

| Settings | Login |
|:---:|:---:|
| ![settings](docs/screenshots/settings.jpg) | ![login](docs/screenshots/login.jpg) |

| Dashboard (dark) | Accounts (dark) | Settings (dark) |
|:---:|:---:|:---:|
| ![dashboard-dark](docs/screenshots/dashboard-dark.jpg) | ![accounts-dark](docs/screenshots/accounts-dark.jpg) | ![settings-dark](docs/screenshots/settings-dark.jpg) |

</details>

## Features

<table>
<tr>
<td><b>Account Pooling</b><br>Load balance across multiple ChatGPT accounts</td>
<td><b>Usage Tracking</b><br>Per-account tokens, cost, 28-day trends</td>
<td><b>API Keys</b><br>Per-key rate limits by token, cost, window, model</td>
</tr>
<tr>
<td><b>Dashboard Auth</b><br>Password + optional TOTP</td>
<td><b>OpenAI-compatible</b><br>Codex CLI, OpenCode, any OpenAI client</td>
<td><b>Auto Model Sync</b><br>Available models fetched from upstream</td>
</tr>
</table>

## Quick Start

```bash
# Docker (recommended)
docker volume create codex-lb-data
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest

# or uvx
uvx codex-lb
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

## Client Setup

Point any OpenAI-compatible client at codex-lb. If [API key auth](#api-key-authentication) is enabled, pass a key from the dashboard as a Bearer token.

| Logo | Client | Endpoint | Config |
|---|--------|----------|--------|
| <img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="32" alt="OpenAI"> | **Codex CLI** | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml` |
| <img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="32" alt="OpenCode"> | **OpenCode** | `http://127.0.0.1:2455/v1` | `~/.config/opencode/opencode.json` |
| <img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="32" alt="OpenClaw"> | **OpenClaw** | `http://127.0.0.1:2455/v1` | `~/.openclaw/openclaw.json` |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="32" alt="Python"> | **OpenAI Python SDK** | `http://127.0.0.1:2455/v1` | Code |

<details>
<summary><img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="20" align="center" alt="OpenAI">&ensp;<b>Codex CLI / IDE Extension</b></summary>
<br>

`~/.codex/config.toml`:

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "OpenAI"  # required — enables remote /responses/compact
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true # required for codex app
```

Optional: enable native upstream WebSockets for Codex streaming while keeping `codex-lb` pooling:

```bash
export CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket
```

`auto` is the default and uses native WebSockets for native Codex headers or models that prefer them.
You can also switch this in the dashboard under Settings -> Routing -> Upstream stream transport.

Note: Codex itself does not currently expose a stable documented `wire_api = "websocket"` provider mode.
If you want to experiment on the Codex side, the current CLI exposes under-development feature flags:

```toml
[features]
responses_websockets = true
# or
responses_websockets_v2 = true
```

These flags are experimental and do not replace `wire_api = "responses"`.

If upstream websocket handshakes must use environment proxies in your deployment, set
`CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=true`. By default websocket handshakes connect directly to
match Codex CLI's native transport.

**With [API key auth](#api-key-authentication):**

```toml
[model_providers.codex-lb]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true # required for codex app
```

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # key from dashboard
codex
```

**Verify WebSocket transport**

Use a one-off debug run:

```bash
RUST_LOG=debug codex exec "Reply with OK only."
```

Healthy websocket signals:

- CLI logs contain `connecting to websocket` and `successfully connected to websocket`
- `codex-lb` logs show `WebSocket /backend-api/codex/responses`
- `codex-lb` logs do **not** show fallback `POST /backend-api/codex/responses` for the same run

If you run `codex-lb` behind a reverse proxy, make sure it forwards WebSocket upgrades.

**Migrating from direct OpenAI** — `codex resume` filters by `model_provider`;
old sessions won't appear until you re-tag them:

```bash
# JSONL session files (all versions)
find ~/.codex/sessions -name '*.jsonl' \
  -exec sed -i '' 's/"model_provider":"openai"/"model_provider":"codex-lb"/g' {} +

# SQLite state DB (>= v0.105.0, creates ~/.codex/state_*.sqlite)
sqlite3 ~/.codex/state_5.sqlite \
  "UPDATE threads SET model_provider = 'codex-lb' WHERE model_provider = 'openai';"
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="20" align="center" alt="OpenCode">&ensp;<b>OpenCode</b></summary>
<br>

> **Important**: Use the built-in `openai` provider with `baseURL` override — not a custom provider with `@ai-sdk/openai-compatible`. Custom providers use the Chat Completions API which **drops reasoning/thinking content**. The built-in `openai` provider uses the Responses API, which properly preserves `encrypted_content` and multi-turn reasoning state.

Before starting, please ensure that all existing OpenAI credentials is cleared in `~/.local/share/opencode/auth.json`
You can clean the config by using this one-liner
`jq 'del(.openai)' ~/.local/share/opencode/auth.json > auth.json.tmp && mv auth.json.tmp ~/.local/share/opencode/auth.json`

`~/.config/opencode/opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openai": {
      "options": {
        "baseURL": "http://127.0.0.1:2455/v1",
        "apiKey": "{env:CODEX_LB_API_KEY}"
      },
      "models": {
        "gpt-5.4": {
          "name": "GPT-5.4",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 1050000, "output": 128000 }
        },
        "gpt-5.3-codex": {
          "name": "GPT-5.3 Codex",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        },
        "gpt-5.1-codex-mini": {
          "name": "GPT-5.1 Codex Mini",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        },
        "gpt-5.3-codex-spark": {
          "name": "GPT-5.3 Codex Spark",
          "reasoning": true,
          "options": { "reasoningEffort": "xhigh", "reasoningSummary": "detailed" },
          "limit": { "context": 128000, "output": 65536 }
        }
      }
    }
  },
  "model": "openai/gpt-5.3-codex"
}
```

This overrides the built-in `openai` provider's endpoint to point at codex-lb while keeping the Responses API code path that handles reasoning properly.

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # key from dashboard
opencode
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="20" align="center" alt="OpenClaw">&ensp;<b>OpenClaw</b></summary>
<br>

`~/.openclaw/openclaw.json`:

```jsonc
{
  "agents": {
    "defaults": {
      "model": { "primary": "codex-lb/gpt-5.4" },
      "models": {
        "codex-lb/gpt-5.4": { "params": { "cacheRetention": "short" } }
        "codex-lb/gpt-5.4-mini": { "params": { "cacheRetention": "short" } }
        "codex-lb/gpt-5.3-codex": { "params": { "cacheRetention": "short" } }
      }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "codex-lb": {
        "baseUrl": "http://127.0.0.1:2455/v1",
        "apiKey": "${CODEX_LB_API_KEY}",   // or "dummy" if API key auth is disabled
        "api": "openai-responses",
        "models": [
          {
            "id": "gpt-5.4",
            "name": "gpt-5.4 (codex-lb)",
            "contextWindow": 1050000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.4-mini",
            "name": "gpt-5.4-mini (codex-lb)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.3-codex",
            "name": "gpt-5.3-codex (codex-lb)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          }
        ]
      }
    }
  }
}
```

Set the env var or replace `${CODEX_LB_API_KEY}` with a key from the dashboard. If API key auth is disabled, any value works.

</details>

<details>
<summary><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="20" align="center" alt="Python">&ensp;<b>OpenAI Python SDK</b></summary>
<br>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # from dashboard, or any string if auth is disabled
)

response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

</details>

## API Key Authentication

API key auth is **disabled by default** — the proxy is open to any client. Enable it in **Settings → API Key Auth** on the dashboard.

When enabled, clients must pass a valid API key as a Bearer token:

```
Authorization: Bearer sk-clb-...
```

**Creating keys**: Dashboard → API Keys → Create. The full key is shown **only once** at creation. Keys support optional expiration, model restrictions, and rate limits (tokens / cost per day / week / month).

## Configuration

Environment variables with `CODEX_LB_` prefix or `.env.local`. See [`.env.example`](.env.example).
Dashboard auth is configured in Settings.
SQLite is the default database backend; PostgreSQL is optional via `CODEX_LB_DATABASE_URL` (for example `postgresql+asyncpg://...`).

## Data

| Environment | Path |
|-------------|------|
| Local / uvx | `~/.codex-lb/` |
| Docker | `/var/lib/codex-lb/` |

Backup this directory to preserve your data.

## Kubernetes

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=changeme \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
kubectl port-forward svc/codex-lb 2455:2455
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

For external database, production config, ingress, observability, and more see the [Helm chart README](deploy/helm/codex-lb/README.md).

## Development

```bash
# Docker
docker compose watch

# Local
uv sync && cd frontend && bun install && cd ..
uv run fastapi run app/main.py --reload        # backend :2455
cd frontend && bun run dev                     # frontend :5173
```

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):
<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Soju06"><img src="https://avatars.githubusercontent.com/u/34199905?v=4?s=100" width="100px;" alt="Soju06"/><br /><sub><b>Soju06</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Soju06" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=Soju06" title="Tests">⚠️</a> <a href="#maintenance-Soju06" title="Maintenance">🚧</a> <a href="#infra-Soju06" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://jonas.kamsker.at/"><img src="https://avatars.githubusercontent.com/u/11245306?v=4?s=100" width="100px;" alt="Jonas Kamsker"/><br /><sub><b>Jonas Kamsker</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=JKamsker" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AJKamsker" title="Bug reports">🐛</a> <a href="#maintenance-JKamsker" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Quack6765"><img src="https://avatars.githubusercontent.com/u/5446230?v=4?s=100" width="100px;" alt="Quack"/><br /><sub><b>Quack</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Quack6765" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AQuack6765" title="Bug reports">🐛</a> <a href="#maintenance-Quack6765" title="Maintenance">🚧</a> <a href="#design-Quack6765" title="Design">🎨</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/hhsw2015"><img src="https://avatars.githubusercontent.com/u/103614420?v=4?s=100" width="100px;" alt="Jill Kok, San Mou"/><br /><sub><b>Jill Kok, San Mou</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=hhsw2015" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=hhsw2015" title="Tests">⚠️</a> <a href="#maintenance-hhsw2015" title="Maintenance">🚧</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Ahhsw2015" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/pcy06"><img src="https://avatars.githubusercontent.com/u/44970486?v=4?s=100" width="100px;" alt="PARK CHANYOUNG"/><br /><sub><b>PARK CHANYOUNG</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=pcy06" title="Documentation">📖</a> <a href="https://github.com/Soju06/codex-lb/commits?author=pcy06" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=pcy06" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/choi138"><img src="https://avatars.githubusercontent.com/u/84369321?v=4?s=100" width="100px;" alt="Choi138"/><br /><sub><b>Choi138</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=choi138" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Achoi138" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=choi138" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/dwnmf"><img src="https://avatars.githubusercontent.com/u/56194792?v=4?s=100" width="100px;" alt="LYA⚚CAP⚚OCEAN"/><br /><sub><b>LYA⚚CAP⚚OCEAN</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=dwnmf" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=dwnmf" title="Tests">⚠️</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/azkore"><img src="https://avatars.githubusercontent.com/u/7746783?v=4?s=100" width="100px;" alt="Eugene Korekin"/><br /><sub><b>Eugene Korekin</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=azkore" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Aazkore" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=azkore" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/JordxnBN"><img src="https://avatars.githubusercontent.com/u/259802500?v=4?s=100" width="100px;" alt="jordan"/><br /><sub><b>jordan</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=JordxnBN" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AJordxnBN" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=JordxnBN" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/DOCaCola"><img src="https://avatars.githubusercontent.com/u/2077396?v=4?s=100" width="100px;" alt="DOCaCola"/><br /><sub><b>DOCaCola</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/issues?q=author%3ADOCaCola" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=DOCaCola" title="Tests">⚠️</a> <a href="https://github.com/Soju06/codex-lb/commits?author=DOCaCola" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/joeblack2k"><img src="https://avatars.githubusercontent.com/u/3456102?v=4?s=100" width="100px;" alt="JoeBlack2k"/><br /><sub><b>JoeBlack2k</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=joeblack2k" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Ajoeblack2k" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=joeblack2k" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/ink-splatters"><img src="https://avatars.githubusercontent.com/u/2706884?v=4?s=100" width="100px;" alt="Peter A."/><br /><sub><b>Peter A.</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=ink-splatters" title="Documentation">📖</a> <a href="https://github.com/Soju06/codex-lb/commits?author=ink-splatters" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Aink-splatters" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/xCatalitY"><img src="https://avatars.githubusercontent.com/u/74815681?v=4?s=100" width="100px;" alt="Hannah Markfort"/><br /><sub><b>Hannah Markfort</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=xCatalitY" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=xCatalitY" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/mws-weekend-projects"><img src="https://avatars.githubusercontent.com/u/255546191?v=4?s=100" width="100px;" alt="mws-weekend-projects"/><br /><sub><b>mws-weekend-projects</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=mws-weekend-projects" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=mws-weekend-projects" title="Tests">⚠️</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="http://hextra.us"><img src="https://avatars.githubusercontent.com/u/88663250?v=4?s=100" width="100px;" alt="Quang Do"/><br /><sub><b>Quang Do</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=quangdo126" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=quangdo126" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/aaiyer"><img src="https://avatars.githubusercontent.com/u/426027?v=4?s=100" width="100px;" alt="Anand Aiyer"/><br /><sub><b>Anand Aiyer</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/issues?q=author%3Aaaiyer" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=aaiyer" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=aaiyer" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/defin85"><img src="https://avatars.githubusercontent.com/u/31535407?v=4?s=100" width="100px;" alt="defin85"/><br /><sub><b>defin85</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=defin85" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Adefin85" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=defin85" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://linktree.huzky.dev/"><img src="https://avatars.githubusercontent.com/u/194083329?v=4?s=100" width="100px;" alt="Jacky Fong"/><br /><sub><b>Jacky Fong</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=huzky-v" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3Ahuzky-v" title="Bug reports">🐛</a> <a href="#question-huzky-v" title="Answering Questions">💬</a> <a href="#maintenance-huzky-v" title="Maintenance">🚧</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/flokosti96"><img src="https://avatars.githubusercontent.com/u/144428350?v=4?s=100" width="100px;" alt="flokosti96"/><br /><sub><b>flokosti96</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=flokosti96" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=flokosti96" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/minpeter"><img src="https://avatars.githubusercontent.com/u/62207008?v=4?s=100" width="100px;" alt="Woonggi Min"/><br /><sub><b>Woonggi Min</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=minpeter" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=minpeter" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://www.linkedin.com/in/yigitkonur/"><img src="https://avatars.githubusercontent.com/u/9989650?v=4?s=100" width="100px;" alt="Yigit Konur"/><br /><sub><b>Yigit Konur</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/issues?q=author%3Ayigitkonur" title="Bug reports">🐛</a> <a href="https://github.com/Soju06/codex-lb/commits?author=yigitkonur" title="Code">💻</a></td>
    </tr>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Daltonganger"><img src="https://avatars.githubusercontent.com/u/17501732?v=4?s=100" width="100px;" alt="Ruben"/><br /><sub><b>Ruben</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Daltonganger" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=Daltonganger" title="Tests">⚠️</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3ADaltonganger" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/L1st3r"><img src="https://avatars.githubusercontent.com/u/336408?v=4?s=100" width="100px;" alt="Steve Santacroce"/><br /><sub><b>Steve Santacroce</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=L1st3r" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=L1st3r" title="Tests">⚠️</a> <a href="https://github.com/Soju06/codex-lb/issues?q=author%3AL1st3r" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/mhughdo"><img src="https://avatars.githubusercontent.com/u/15611134?v=4?s=100" width="100px;" alt="Hugh Do"/><br /><sub><b>Hugh Do</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=mhughdo" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=mhughdo" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/salwinh"><img src="https://avatars.githubusercontent.com/u/6965142?v=4?s=100" width="100px;" alt="Hubert Salwin"/><br /><sub><b>Hubert Salwin</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=salwinh" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=salwinh" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Daeroni"><img src="https://avatars.githubusercontent.com/u/1648961?v=4?s=100" width="100px;" alt="Teemu Koskinen"/><br /><sub><b>Teemu Koskinen</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Daeroni" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://felixypz.me"><img src="https://avatars.githubusercontent.com/u/151984457?v=4?s=100" width="100px;" alt="Yu Peng Zheng"/><br /><sub><b>Yu Peng Zheng</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=Felix201209" title="Documentation">📖</a> <a href="https://github.com/Soju06/codex-lb/commits?author=Felix201209" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/embogomolov"><img src="https://avatars.githubusercontent.com/u/185256086?v=4?s=100" width="100px;" alt="embogomolov"/><br /><sub><b>embogomolov</b></sub></a><br /><a href="https://github.com/Soju06/codex-lb/commits?author=embogomolov" title="Code">💻</a> <a href="https://github.com/Soju06/codex-lb/commits?author=embogomolov" title="Tests">⚠️</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
