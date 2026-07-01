# CodexCont

[English](CODEXCONT.md) . [Chinese](README_zh.md)

Continue-thinking middleware for Codex / OpenAI Responses-compatible APIs.

CodexCont detects the observed reasoning-truncation fingerprint `usage.output_tokens_details.reasoning_tokens == 518 * n - 2`, asks the model to continue thinking, and folds multiple upstream streaming responses into one downstream SSE response.

In codex-lb, that folding is passively integrated into the normal Responses-compatible HTTP stream path and is enabled by default. The standalone Starlette proxy is still available for isolated use.

```text
Coding agent  ->  codex-lb Responses stream  ->  CodexCont fold  ->  Codex / Responses API
```

> Installing via an AI agent? Hand it [`INSTALL-GUIDE-AGENT/AGENT.md`](INSTALL-GUIDE-AGENT/AGENT.md), a step-by-step runbook written for an AI agent to execute on your machine.

## Disclaimer

This middleware explicitly bypasses the observed OpenAI Codex reasoning-truncation behavior. If using it is considered abusive, violates service terms, increases costs unexpectedly, or causes other adverse consequences, those consequences are the user's responsibility.

## What it does

- Streams reasoning items to the agent live.
- Buffers tentative final output (`message` and `function_call`) until the upstream terminal event reveals whether the round was truncated.
- If the round is truncated, discards tentative output and opens a continuation round with prior reasoning replayed.
- If the round finishes cleanly or a safety cap is reached, flushes final-round output and emits one reconstructed terminal response.
- Leaves non-matching traffic as a transparent passthrough.

The default continuation method is a hidden `phase: commentary` assistant message using `Continue thinking...`. A legacy synthetic tool-pair mode is also available.

## Requirements

- Python `>= 3.12` for CodexCont itself; this repository currently targets Python `>= 3.13`.
- [`uv`](https://docs.astral.sh/uv/) is recommended.
- Runtime dependencies: `httpx`, `starlette`, and `uvicorn`.

## Quick Start

### Integrated codex-lb path

The codex-lb integration is controlled by `CODEX_LB_CODEX_CONTINUATION_*` settings:

- `CODEX_LB_CODEX_CONTINUATION_ENABLED=true` enables passive folding.
- `CODEX_LB_CODEX_CONTINUATION_MAX_CONTINUE=3` caps hidden continuation rounds after round 1.
- `CODEX_LB_CODEX_CONTINUATION_BYPASS_HTTP_BRIDGE=true` routes continuation-eligible HTTP streams through the standard stream path so the fold cannot be bypassed by the HTTP session bridge.
- `CODEX_LB_CODEX_CONTINUATION_FORCE_INCLUDE_ENCRYPTED=true` ensures continuation rounds request encrypted reasoning content.

Hidden continuation rounds reuse the selected upstream account/auth route inside the same service stream. They are not exposed as separate downstream responses.

### Standalone proxy

```bash
uv sync
cp config.example.toml config.toml
uv run python run.py
```

`run.py` reads the local `config.toml`. The example configuration listens on `127.0.0.1:8787` and accepts POST requests at `/v1/responses`.

## Point A Client At The Proxy

Use the proxy URL instead of the real upstream URL:

```text
http://127.0.0.1:8787/v1/responses
```

The example default configuration uses:

```toml
[upstream]
url = "https://chatgpt.com/backend-api/codex/responses"
mode = "header"
```

With `mode = header`, a `Responses-API-Base` request header overrides the configured upstream URL. The middleware appends `/responses` unless the supplied value already ends with `/responses`, and strips that control header before forwarding upstream.

## Authentication

`config.toml` supports three auth modes:

- `passthrough`: forward the caller's auth headers only.
- `inject`: override or set auth headers from config.
- `passthrough_then_inject`: keep caller auth when present, otherwise inject from config.

Security guard: if a request supplies `Responses-API-Base`, the middleware will not leak configured credentials to that request-supplied URL. If the current auth mode would inject configured credentials for that request, it rejects the request with `400`.

Do not commit secrets. `config.toml`, `rt.json`, and `free_rt.json` are ignored by `.gitignore`.

## When Continuation Is Applied

The integrated path folds Responses-compatible HTTP streams when all of the following are true:

- `CODEX_LB_CODEX_CONTINUATION_ENABLED=true`.
- The request is handled by codex-lb's HTTP Responses stream path.
- Streaming is enabled by the route.
- Reasoning is not explicitly disabled.

All other requests are proxied without continuation folding. The standalone proxy uses the matching `[continue]` and `[stream]` keys from `config.toml`.

## Response Metadata

The final reconstructed response includes proxy metadata such as:

- `metadata.proxy_rounds`: per-round reasoning token counts and detected tier `n`.
- `metadata.proxy_billed_usage`: summed upstream token usage across hidden rounds.
- `metadata.proxy_stopped_reason`: present when a guard or error stops continuation.

Agent-facing `usage` is reconstructed to look like one response: round-1 input and cached tokens, summed reasoning tokens, and final-round non-reasoning output.

## Tests

The imported test suite is self-contained and can run without pytest:

```bash
uv run python tests/test_middleware.py
```

Coverage includes truncation math, incremental SSE parsing, fold/rewrite behavior with captured SSE fixtures, commentary and tool-pair continuation payloads, header transparency, upstream URL resolution, auth safety guards, and EOF/upstream-error behavior.

## Project Layout

```text
middleware/
  app.py       # Starlette app and route handler
  codex.py     # truncation math and continuation payload builders
  config.py    # config.toml loader and dataclasses
  creds.py     # upstream header/auth construction
  proxy.py     # fold_stream state machine
  sse.py       # incremental SSE parser/serializer
  store.py     # in-memory ID store for optional stateful repair

tests/
  test_middleware.py
  fixtures/

run.py         # uvicorn entrypoint
config.example.toml
```
