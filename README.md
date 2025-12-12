# codex-lb

Load balancer for ChatGPT accounts. Pool multiple accounts, track usage, view everything in a dashboard.

## Quick Start

### Docker

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v ~/.codex-lb:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

### uvx

```bash
uvx codex-lb
```

Open [localhost:2455](http://localhost:2455) → Add account → Done.

## Codex CLI & Extension Setup

Add to `~/.codex/config.toml`:

```toml
model = "gpt-5.2-codex"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "OpenAI"  # MUST be "OpenAI" - enables /compact endpoint
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
chatgpt_base_url = "http://127.0.0.1:2455"
requires_openai_auth = true  # Required: enables model selection in Codex IDE extension
```

## Data

All data stored in `~/.codex-lb/`:
- `store.db` – accounts, usage logs
- `encryption.key` – encrypts tokens (auto-generated)

Backup this directory to preserve your accounts.
