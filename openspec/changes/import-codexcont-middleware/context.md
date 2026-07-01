## Purpose And Scope

This change imports CodexCont into the codex-lb repository and wires its
continuation folding into codex-lb's normal Responses-compatible HTTP stream
path. The integrated path is enabled by default and controlled with
`CODEX_LB_CODEX_CONTINUATION_*` settings.

The standalone Starlette middleware remains available for isolated operation:
operators can run it directly with `uv run python run.py` after copying
`config.example.toml` to `config.toml`.

The scope is the CodexCont runtime surface, configuration, documentation,
fixtures, packaging metadata, codex-lb core stream integration, and HTTP bridge
bypass needed to keep continuation folding passive for HTTP Responses streams.

## Decisions

- Keep `README.md` as the codex-lb landing page and add `CODEXCONT.md` for the
  imported middleware rather than replacing the project identity.
- Keep `config.toml`, `rt.json`, and `free_rt.json` ignored because they may
  contain local runtime secrets or account tokens.
- Package `middleware` with the existing wheel target so local installs can import
  the modules used by `run.py` and the tests.
- Keep CodexCont's test harness self-contained so the imported behavior can be
  verified without live upstream calls.
- Reuse CodexCont's truncation detector and continuation payload builder from
  the vendored package, while keeping codex-lb-specific stream folding under
  `app/core/clients/` so it can use codex-lb settings, SSE formatting, and
  upstream routing.
- Apply continuation after codex-lb account selection has chosen an upstream
  account and route. Hidden continuation rounds reuse that access token, account
  id, route, Codex client, and HTTP session.
- Bypass the HTTP session bridge for continuation-eligible requests by default.
  The bridge streams from a live upstream websocket session and would otherwise
  skip the core Responses stream fold.

## Constraints And Failure Modes

- The continuation detector is intentionally narrow: it only treats reasoning
  token counts matching `truncation_step * n - 2` as truncation candidates.
- Final output is buffered until the terminal upstream event proves whether the
  round was truncated, so final-answer latency can increase.
- Hidden continuation rounds can increase billed upstream usage. The
  reconstructed terminal response includes proxy metadata with per-round details
  and summed billed usage.
- Requests that fail the continuation gates are proxied without continuation
  folding.
- The passive integration covers codex-lb HTTP Responses-compatible streams. The
  standalone middleware remains available as a separate Starlette runtime.
- Header-selected upstream URLs must never receive configured proxy credentials.
  Callers using per-request upstream overrides need to provide their own
  authorization headers or use passthrough-safe auth modes.
- Stateful repair uses in-memory process-local storage and is not shared across
  multiple middleware processes.

## Example

```bash
cp config.example.toml config.toml
uv run python run.py
```

With the default example config, send streaming Responses-compatible requests to
`http://127.0.0.1:8787/v1/responses`. To override the upstream per request, send
`Responses-API-Base: https://api.openai.com/v1`; the middleware appends
`/responses` when needed and strips the control header before forwarding.

For the integrated codex-lb path, no separate listener is required. Send normal
HTTP Responses-compatible requests through codex-lb. Set
`CODEX_LB_CODEX_CONTINUATION_ENABLED=false` to disable passive folding.
