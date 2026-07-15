# Client Setup

Point any OpenAI-compatible client at codex-lb. If [API key auth](api-keys.md) is enabled, pass a key from the dashboard as a Bearer token.

Model availability is discovered from the upstream Codex model catalog and can vary by account plan, workspace, rollout, and upstream deprecation state. Prefer the live `GET /v1/models` or `GET /backend-api/codex/models` response over a copied static table when configuring clients or API-key model allowlists.

The examples below use the current frontier lineup: **`gpt-5.6-sol`** (strongest), **`gpt-5.6-terra`** (balanced), and **`gpt-5.6-luna`** (fast) — all 372k context. `gpt-5.5` and `gpt-5.4` are still served for older pinned clients; retired slugs such as `gpt-5.3-codex`, `gpt-5.3-codex-spark`, and `gpt-5.1-codex-mini` were dropped from the upstream bundled catalog and should no longer be used in new configs.

| Client | Endpoint | Config |
|--------|----------|--------|
| [Codex CLI](#codex-cli-ide-extension) | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml` |
| [OpenCode](#opencode) | `http://127.0.0.1:2455/v1` | `~/.config/opencode/opencode.json` |
| [OpenClaw](#openclaw) | `http://127.0.0.1:2455/v1` | `~/.openclaw/openclaw.json` |
| [Hermes Agent](#hermes-agent) | `http://127.0.0.1:2455/v1` | `~/.hermes/config.yaml` |
| [OpenAI Python SDK](#openai-python-sdk) | `http://127.0.0.1:2455/v1` | Code |

## Codex CLI / IDE Extension

`~/.codex/config.toml`:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "openai"  # required — enables remote /responses/compact. Lowercase since Codex 2026-05-23; older "OpenAI" stops resolving gpt-5.5
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true # required for codex app
```

This documented `requires_openai_auth = true` setup uses Codex-backed authentication and does not need an `x-openai-actor-authorization` marker to be eligible for Codex's built-in `$imagegen` tool. Provider configurations that intentionally skip OpenAI login have a different eligibility path; see the [Images compatibility context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/images-api-compat/context.md#codex-provider-eligibility).

### WebSocket transport

Optional: enable native upstream WebSockets for Codex streaming while keeping `codex-lb` pooling:

```bash
export CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket
```

`auto` is the default and uses native WebSockets for native Codex headers or models that prefer them.
You can also switch this in the dashboard under Settings → Routing → Upstream stream transport.

Note: Codex itself does not currently expose a stable documented `wire_api = "websocket"` provider mode.
If you want to experiment on the Codex side, the current CLI exposes under-development feature flags:

```toml
[features]
responses_websockets = true
# or
responses_websockets_v2 = true
```

These flags are experimental and do not replace `wire_api = "responses"`.

Upstream websocket handshakes automatically honor standard proxy environment variables when they are
present. `wss://` handshakes check `wss_proxy`, `socks_proxy`, `https_proxy`, and `all_proxy`;
plain `ws://` handshakes also check `ws_proxy` and `http_proxy`. Set
`CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=false` only when websocket handshakes must bypass those
environment proxies and connect directly.

### With API key auth

When [API key auth](api-keys.md) is enabled:

```toml
[model_providers.codex-lb]
name = "openai"
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

### Verify WebSocket transport

Use a one-off debug run:

```bash
RUST_LOG=debug codex exec "Reply with OK only."
```

Healthy websocket signals:

- CLI logs contain `connecting to websocket` and `successfully connected to websocket`
- `codex-lb` logs show `WebSocket /backend-api/codex/responses`
- `codex-lb` logs do **not** show fallback `POST /backend-api/codex/responses` for the same run

If you run `codex-lb` behind a reverse proxy, make sure it forwards WebSocket upgrades — see [Remote Access](deployment/remote.md).

### Migrating from direct OpenAI (session retagging)

`codex resume` filters by `model_provider`; old sessions won't appear until you re-tag them. Use the built-in retag command instead of editing Codex files by hand; see [Codex session retagging](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/runtime-portability/context.md#codex-session-retagging) for backups, Docker, WSL, and rollback details.

```bash
# Preview what will change first.
codex-lb codex-sessions retag --from openai --to codex-lb --dry-run

# Then close Codex/Codex CLI and apply the retag.
codex-lb codex-sessions retag --from openai --to codex-lb --yes
```

| Dry run (Docker) | Apply (Docker) |
|:---:|:---:|
| ![retag dry run in Docker](screenshots/codex-session-retag-docker-dry-run.png) | ![retag apply in Docker](screenshots/codex-session-retag-docker-apply.png) |

| Dry run (WSL) | Apply (WSL) |
|:---:|:---:|
| ![retag dry run in WSL](screenshots/codex-session-retag-wsl-dry-run.png) | ![retag apply in WSL](screenshots/codex-session-retag-wsl-apply.png) |

## OpenCode

!!! important
    Use the built-in `openai` provider with `baseURL` override — not a custom provider with `@ai-sdk/openai-compatible`. Custom providers use the Chat Completions API which **drops reasoning/thinking content**. The built-in `openai` provider uses the Responses API, which properly preserves `encrypted_content` and multi-turn reasoning state.

Before starting, please ensure that all existing OpenAI credentials are cleared in `~/.local/share/opencode/auth.json`.
You can clean the config by using this one-liner:

```bash
jq 'del(.openai)' ~/.local/share/opencode/auth.json > auth.json.tmp && mv auth.json.tmp ~/.local/share/opencode/auth.json
```

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
        "gpt-5.6-sol": {
          "name": "GPT-5.6-Sol",
          "reasoning": true,
          "options": { "reasoningEffort": "xhigh", "reasoningSummary": "detailed" },
          "limit": { "context": 372000, "output": 65536 }
        },
        "gpt-5.6-terra": {
          "name": "GPT-5.6-Terra",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 372000, "output": 65536 }
        },
        "gpt-5.6-luna": {
          "name": "GPT-5.6-Luna",
          "reasoning": true,
          "options": { "reasoningEffort": "medium", "reasoningSummary": "detailed" },
          "limit": { "context": 372000, "output": 65536 }
        },
        "gpt-5.5": {
          "name": "GPT-5.5",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        }
      }
    }
  },
  "model": "openai/gpt-5.6-sol"
}
```

This overrides the built-in `openai` provider's endpoint to point at codex-lb while keeping the Responses API code path that handles reasoning properly.

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # key from dashboard
opencode
```

## OpenClaw

`~/.openclaw/openclaw.json`:

```jsonc
{
  "agents": {
    "defaults": {
      "model": { "primary": "codex-lb/gpt-5.6-sol" },
      "models": {
        "codex-lb/gpt-5.6-sol": { "params": { "cacheRetention": "short" } },
        "codex-lb/gpt-5.6-terra": { "params": { "cacheRetention": "short" } },
        "codex-lb/gpt-5.6-luna": { "params": { "cacheRetention": "short" } }
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
            "id": "gpt-5.6-sol",
            "name": "gpt-5.6-sol (codex-lb)",
            "contextWindow": 372000,
            "contextTokens": 372000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.6-terra",
            "name": "gpt-5.6-terra (codex-lb)",
            "contextWindow": 372000,
            "contextTokens": 372000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.6-luna",
            "name": "gpt-5.6-luna (codex-lb)",
            "contextWindow": 372000,
            "contextTokens": 372000,
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

Set the env var or replace `${CODEX_LB_API_KEY}` with a key from the dashboard. If API key auth is disabled,
local requests can omit the key, but non-local requests are still rejected until proxy authentication is configured.

The `/v1` route is the simplest OpenAI-compatible setup. If your OpenClaw build uses a Codex-native provider path such as `openai-codex-responses` and needs Codex-style usage/accounting behavior, point that provider at `http://127.0.0.1:2455/backend-api/codex` instead. For third-party Codex-compatible backends, the client must allow opaque bearer-token passthrough and should only send `chatgpt-account-id` when it actually decoded one from an official ChatGPT/Codex token.

## Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) works with any model provider; point a named custom provider at codex-lb with the `codex_responses` API mode so multi-turn reasoning state is preserved over the Responses API (the plain `chat_completions` mode drops reasoning content, same caveat as OpenCode).

`~/.hermes/config.yaml`:

```yaml
custom_providers:
  - name: codex-lb
    base_url: http://127.0.0.1:2455/v1
    key_env: CODEX_LB_API_KEY   # omit for local runs without API key auth
    api_mode: codex_responses
```

Then select the model interactively with `hermes model`, or in a session:

```text
/model custom:codex-lb:gpt-5.6-sol
```

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # key from dashboard
hermes
```

## OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # from dashboard, or any non-empty string if auth is disabled
)

response = client.chat.completions.create(
    model="gpt-5.6-terra",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

---

*Specs: [responses-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat) · [chat-completions-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/chat-completions-compat) · [model-catalog-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-catalog-compat) · [runtime-portability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/runtime-portability)*
