# Client Setup

Point OpenAI-compatible clients directly at codex-lb. If [API key auth](api-keys.md) is enabled, pass a key from the dashboard as a Bearer token. Claude Code is the exception below: it needs an external protocol-translating gateway.

Model availability is discovered from the upstream Codex model catalog and can vary by account plan, workspace, rollout, and upstream deprecation state. Prefer the live `GET /v1/models` or `GET /backend-api/codex/models` response over a copied static table when configuring clients or API-key model allowlists.

The examples below use the current frontier lineup: **`gpt-5.6-sol`** (strongest), **`gpt-5.6-terra`** (balanced), and **`gpt-5.6-luna`** (fast) — all with a 272k default input budget and an 872k upstream maximum ([opt-in, Codex CLI only](#opting-into-the-872k-context-window)). `gpt-5.5` and `gpt-5.4` are still served for older pinned clients; retired slugs such as `gpt-5.3-codex`, `gpt-5.3-codex-spark`, and `gpt-5.1-codex-mini` were dropped from the upstream bundled catalog and should no longer be used in new configs.

| Client | Endpoint | Config |
|--------|----------|--------|
| [Codex CLI](#codex-cli-ide-extension) | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml` |
| [OpenCode](#opencode) | `http://127.0.0.1:2455/v1` | `~/.config/opencode/opencode.json` |
| [OpenClaw](#openclaw) | `http://127.0.0.1:2455/v1` | `~/.openclaw/openclaw.json` |
| [Hermes Agent](#hermes-agent) | `http://127.0.0.1:2455/v1` | `~/.hermes/config.yaml` |
| [Claude Code](#claude-code-experimental-external-gateway) | `http://127.0.0.1:4000/v1/messages` | Experimental external gateway |
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

### Opting into the 872k context window

GPT-5.6 ships a 272,000-token default input budget with an 872,000-token
maximum. codex-lb advertises both — `context_window` and `max_context_window`
on `GET /backend-api/codex/models` — and the Codex CLI stays on the default
until you raise it in `~/.codex/config.toml` (top level, before any
`[section]` header):

```toml
model_context_window = 872000
```

- Values above `max_context_window` are clamped to it: `model_context_window =
  1000000` resolves to 872,000 and does not unlock a 1M window.
- Leave `model_auto_compact_token_limit` unset. Codex auto-compacts at 90% of
  the resolved window — 784,800 tokens here — and clamps any larger configured
  value down to that, so setting `900000` is a no-op. Set it only to compact
  *earlier*.
- Cost: input beyond the 272,000-token threshold is metered at the upstream
  long-context rate. That threshold is why 272,000 stays the default.

These keys are Codex-CLI-only. The OpenCode / OpenClaw / SDK examples below
stay at 272000 because `/v1/models` reports the default input budget, not the
ceiling.

### Daybreak Blue profile (Trusted Access)

Use a separate provider for authorized defensive cybersecurity work. The
ordinary `codex-lb` provider above must remain free of the capability header;
adding it there would classify every request as requiring the restricted pool.

First add this opt-in provider to the same machine-local
`~/.codex/config.toml`:

```toml
[model_providers.codex-lb-daybreak-blue]
name = "openai"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true
http_headers = { "X-Codex-LB-Required-Capability" = "trusted_cyber" }
```

Then create `~/.codex/daybreak-blue.config.toml`:

```toml
model = "gpt-5.6-sol"
model_provider = "codex-lb-daybreak-blue"
```

Activate it explicitly for the task or orchestration root that needs the
restricted route:

```bash
export CODEX_LB_API_KEY="sk-clb-..." # key from the dashboard
codex --profile daybreak-blue
codex exec --profile daybreak-blue "<authorized defensive task>"
```

Current Codex versions load named profiles from sibling
`<profile>.config.toml` files; legacy `[profiles.<name>]` tables are no longer
selected. Provider and profile keys are machine-local, so a project
`.codex/config.toml` cannot activate this route. See the official
[Codex profile documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).

The static header is an authenticated routing requirement, not a grant. Use
this profile only when the selected identity and ChatGPT workspace or API
organization/project are already approved for the intended Codex product
surface. The dedicated provider always supplies a Codex LB API key because
unauthenticated capability carriers are rejected even on a local deployment.
When the capability header is present, Codex LB validates that key for the
request even if global API-key auth is disabled; ordinary requests without the
header keep the deployment's normal auth behavior. Current Codex clients may
fall back from WebSocket to HTTP even when `supports_websockets = true`, and
static provider headers also accompany control and Images requests. Codex LB
authenticates capability-bearing HTTP and non-Responses WebSocket requests and
then rejects them with `required_capability_transport_unsupported` before
account selection or upstream dispatch. This includes Responses/compact HTTP
fallback, Codex control, admission, warmup, files, transcription, Chat
Completions, Images, reset-credit consume, and Live WebSockets; Chat Completions
is guarded defensively if a provider client reaches that equivalent routing
sink. Authenticated `/models` initialization and local API-key usage or
reset-credit listings remain available because they do not route an upstream
account. Restore direct Responses WebSocket availability
instead of removing the carrier or retrying through ordinary HTTP. Codex LB
narrows a direct WebSocket turn's first and later account selections to eligible accounts already marked
`security_work_authorized`; if none are available, it fails closed without
ordinary fallback. Selecting `gpt-5.6-sol` by itself does not activate this
path, and a Daybreak alias may resolve to that same underlying model. See
OpenAI's
[Trusted Access guidance](https://developers.openai.com/api/docs/guides/safety-checks/cybersecurity#authorized-access-and-agentic-workflows).

Complete inert examples are available as
[`config.toml`](examples/codex/config.toml) and
[`daybreak-blue.config.toml`](examples/codex/daybreak-blue.config.toml). To
roll back, stop using `--profile daybreak-blue`, remove the profile file, and
optionally remove only the `codex-lb-daybreak-blue` provider block. No server or
database change is required.

This documented `requires_openai_auth = true` setup makes the provider eligible
for Codex's built-in `$imagegen` tool, but the Daybreak carrier is intentionally
rejected on the Images HTTP routes before their ordinary account-routing
pipeline. Consequently `$imagegen` fails closed inside the Daybreak profile;
do not remove the carrier to make it work during a restricted task. Use the
ordinary provider only for separate work that does not require Daybreak
routing. Provider configurations that intentionally skip OpenAI login have a
different eligibility path; see the [Images compatibility context](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/images-api-compat/context.md#codex-provider-eligibility).

### WebSocket transport

Optional: enable native upstream WebSockets for Codex streaming while keeping `codex-lb` pooling:

```bash
export CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket
```

`auto` is the default and uses native WebSockets for native Codex headers or models that prefer them.
You can also switch this in the dashboard under Settings → Routing → Upstream stream transport.

Note: Codex itself does not currently expose a stable documented
`wire_api = "websocket"` or WebSocket-only provider mode.
`supports_websockets = true` enables WebSocket attempts but does not disable
HTTP fallback. Removed `responses_websockets` feature flags are not a
fail-closed transport control.

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
          "limit": { "context": 272000, "output": 65536 }
        },
        "gpt-5.6-terra": {
          "name": "GPT-5.6-Terra",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        },
        "gpt-5.6-luna": {
          "name": "GPT-5.6-Luna",
          "reasoning": true,
          "options": { "reasoningEffort": "medium", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
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
            "contextWindow": 272000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.6-terra",
            "name": "gpt-5.6-terra (codex-lb)",
            "contextWindow": 272000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.6-luna",
            "name": "gpt-5.6-luna (codex-lb)",
            "contextWindow": 272000,
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

## Claude Code (experimental external gateway)

!!! warning
    Anthropic does not support routing Claude Code to non-Claude models through
    a gateway. This is an experimental, community-owned composition; see
    [Anthropic's gateway policy](https://docs.anthropic.com/en/docs/claude-code/llm-gateway)
    and the [codex-lb maintainer decision](https://github.com/Soju06/codex-lb/issues/114#issuecomment-4988534309).

codex-lb does not implement Anthropic's Messages API or manage Claude
credentials. To use Claude Code's interface with a model served by codex-lb,
run an Anthropic-to-OpenAI translator in front:

```text
Claude Code -> LiteLLM /v1/messages -> codex-lb /v1/responses
```

This adds neither Claude models nor Anthropic credentials to codex-lb.
LiteLLM owns the Messages, streaming, tool, and error translation.

### Configure the translator

Create `litellm.yaml` outside the codex-lb repository:

```yaml
model_list:
  - model_name: codex-lb-gpt-5.6-sol
    litellm_params:
      model: openai/gpt-5.6-sol
      api_base: os.environ/CODEX_LB_BASE_URL
      api_key: os.environ/CODEX_LB_API_KEY

litellm_settings:
  use_chat_completions_url_for_anthropic_messages: false

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

The public `codex-lb-gpt-5.6-sol` alias maps to `gpt-5.6-sol` through
codex-lb. The target must support Responses: choose a model from
`GET /backend-api/codex/models`, or verify that an external source accepts
`POST /v1/responses`. `GET /v1/models` can also list
Chat-Completions-only sources, which do not work with this recipe.

Keep the `openai/` provider prefix and the explicit LiteLLM setting. Marking
the model as natively supporting `/v1/messages` would pass Anthropic
payloads to codex-lb without translation.

Set separate credentials for the two hops, then bind LiteLLM to loopback:

```bash
export CODEX_LB_BASE_URL="http://127.0.0.1:2455/v1"
export CODEX_LB_API_KEY="sk-clb-..."        # dashboard key; see loopback note below
export LITELLM_MASTER_KEY="sk-litellm-..." # choose a separate random secret

env -u DEBUG uvx --from 'litellm[proxy]==1.97.0' \
  --with 'fastapi==0.140.0' \
  litellm \
  --config ./litellm.yaml \
  --host 127.0.0.1 \
  --port 4000
```

The FastAPI pin avoids a startup incompatibility observed with FastAPI
`0.141.1`. The scoped `env -u DEBUG` prevents an unrelated shell `DEBUG`
value from being parsed as LiteLLM's Boolean CLI option. Transitive
dependencies remain ranged, so repeat the smoke test when the environment is
recreated.

Do not reuse a Claude.ai OAuth token or Anthropic Console key for either
value. `CODEX_LB_API_KEY` authenticates LiteLLM to codex-lb;
`LITELLM_MASTER_KEY` authenticates Claude Code to LiteLLM. A non-empty
placeholder works only when LiteLLM reaches an auth-disabled codex-lb over
same-host loopback. Across containers, use their private network and enable
codex-lb API-key auth with a registered dashboard key. Auth-disabled codex-lb
rejects non-loopback proxy requests unless the raw peer is explicitly
allowlisted with `CODEX_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS`. Do not expose
an unencrypted LiteLLM listener or either key to an untrusted network.

### Verify the translation path

With LiteLLM running, open another shell, re-export the same master key, and
send a streamed Anthropic-format request:

```bash
export LITELLM_MASTER_KEY="sk-litellm-..." # same value used to start LiteLLM

curl --fail-with-body --silent --show-error --no-buffer \
  http://127.0.0.1:4000/v1/messages \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  -d '{
    "model": "codex-lb-gpt-5.6-sol",
    "max_tokens": 32,
    "stream": true,
    "messages": [{"role": "user", "content": "Reply with OK only."}]
  }'
```

A successful stream contains Anthropic SSE events from `message_start`
through `message_stop`. LiteLLM should log the public alias, and codex-lb
should record a `POST /v1/responses` request for the serving account. This
uses real quota.

This recipe was prepared with Claude Code `2.1.220`, LiteLLM proxy
`1.97.0`, and FastAPI `0.140.0`. Non-streaming, streaming, tool-use, and
Claude one-shot translation were exercised against a local mock Responses
endpoint; that does not replace the live-deployment smoke request above.
Re-test when upgrading either proxy dependency or Claude Code.

### Point Claude Code at LiteLLM

Map the Opus, Sonnet, and Haiku aliases to the configured public alias, and
force subagents onto it so helper requests do not use an unconfigured Claude
model name. In the shell that will run Claude Code, re-export the same LiteLLM
key:

```bash
export LITELLM_MASTER_KEY="sk-litellm-..." # same value used to start LiteLLM
export ANTHROPIC_BASE_URL="http://127.0.0.1:4000"
export ANTHROPIC_AUTH_TOKEN="$LITELLM_MASTER_KEY"
export ANTHROPIC_DEFAULT_OPUS_MODEL="codex-lb-gpt-5.6-sol"
export ANTHROPIC_DEFAULT_SONNET_MODEL="codex-lb-gpt-5.6-sol"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="codex-lb-gpt-5.6-sol"
export CLAUDE_CODE_SUBAGENT_MODEL="codex-lb-gpt-5.6-sol"

claude --model codex-lb-gpt-5.6-sol
```

`CLAUDE_CODE_SUBAGENT_MODEL` overrides per-agent model selection for this
gateway session, including subagents that request the `fable` alias. This keeps
agent teams and workflows on the one model LiteLLM exposes in this recipe. It
does not redirect a main-session `/model fable` selection; keep the main session
on the public alias shown above.
`ANTHROPIC_DEFAULT_FABLE_MODEL` is intentionally unset. Claude Code also
uses that variable to identify a target as Fable 5 for automatic fallback,
which this non-Claude alias does not implement.

`ANTHROPIC_AUTH_TOKEN` sends the LiteLLM key as a Bearer credential and
takes precedence over a saved claude.ai login. Existing Claude Code settings
can override shell variables; if they do, put the gateway values in an
explicit settings `env` block. Run `/status` and confirm that a gateway
credential is active. Follow Anthropic's
[gateway setup](https://docs.anthropic.com/en/docs/claude-code/llm-gateway-connect)
for persistent configuration, and unset these values to restore the previous
login path.

### Compatibility boundaries

- Effective features and limits are the intersection of Claude Code,
  LiteLLM's translation, codex-lb policy, and the mapped Responses model.
- These family alias mappings can make Claude Code apply Claude-specific
  context-window and compaction assumptions to the non-Claude target. If you set
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, derive and test it against the effective
  deployment limit; this recipe does not prescribe a value.
- LiteLLM `1.97.0` does not preserve Responses reasoning identity or
  signatures across Messages turns. Translated thinking can become summary or
  ordinary text; do not assume encrypted reasoning or prompt-cache continuity.
- LiteLLM serves `/v1/messages/count_tokens`, but codex-lb does not implement
  `/v1/responses/input_tokens`. After that request returns `404`, this
  LiteLLM version estimates locally and can omit separate system prompts or
  tool definitions, materially undercounting the real input.
- Anthropic error-envelope fidelity, beta fields, tool references, web search,
  extended thinking, and prompt caching can degrade across the translation.
- codex-lb API-key model allowlists, account assignment, and limits still
  apply. Claude.ai identity features are unavailable with the gateway
  credential, and optional Claude Code background traffic is not an
  egress-isolation boundary.

Common failures:

| Symptom | Check |
| --- | --- |
| LiteLLM returns `401` | `ANTHROPIC_AUTH_TOKEN` must equal `LITELLM_MASTER_KEY`. |
| codex-lb returns `401` | Use a valid dashboard `CODEX_LB_API_KEY`. An auth-disabled placeholder works only over loopback; across containers, enable key auth or explicitly allowlist the raw peer. |
| LiteLLM reports an unknown model | Match `model_name`, the three configured `ANTHROPIC_DEFAULT_*_MODEL` values, `CLAUDE_CODE_SUBAGENT_MODEL`, and `claude --model`. |
| LiteLLM cannot reach codex-lb | Keep `/v1` in `CODEX_LB_BASE_URL`; across containers, replace loopback with a private-network host. |
| Claude Code still contacts Anthropic for model requests | Remove conflicting saved settings or use an explicit settings `env` block, then verify `/status`. |

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

*Specs: [responses-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat) · [images-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/images-api-compat) · [chat-completions-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/chat-completions-compat) · [realtime-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/realtime-api-compat) · [proxy-admission-control](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-admission-control) · [proxy-warmup](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-warmup) · [files-upload-protocol](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/files-upload-protocol) · [audio-transcriptions-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/audio-transcriptions-compat) · [model-catalog-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-catalog-compat) · [runtime-portability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/runtime-portability)*
