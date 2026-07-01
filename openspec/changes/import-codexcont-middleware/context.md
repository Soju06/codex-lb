## Purpose And Scope

This change imports CodexCont as an optional standalone middleware inside the
codex-lb repository. The imported middleware is separate from the existing
codex-lb FastAPI application: operators run it directly with `uv run python
run.py` after copying `config.example.toml` to `config.toml`.

The scope is the CodexCont runtime surface, configuration, documentation,
fixtures, and packaging metadata. It does not wire the continuation behavior into
codex-lb's existing `/backend-api/codex/*` or `/v1/*` proxy routes.

## Decisions

- Keep `README.md` as the codex-lb landing page and add `CODEXCONT.md` for the
  imported middleware rather than replacing the project identity.
- Keep `config.toml`, `rt.json`, and `free_rt.json` ignored because they may
  contain local runtime secrets or account tokens.
- Package `middleware` with the existing wheel target so local installs can import
  the modules used by `run.py` and the tests.
- Keep CodexCont's test harness self-contained so the imported behavior can be
  verified without live upstream calls.

## Constraints And Failure Modes

- The continuation detector is intentionally narrow: it only treats reasoning
  token counts matching `truncation_step * n - 2` as truncation candidates.
- Final output is buffered until the terminal upstream event proves whether the
  round was truncated, so final-answer latency can increase.
- Non-streaming requests and requests that fail the continuation gates are
  transparent passthroughs.
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
