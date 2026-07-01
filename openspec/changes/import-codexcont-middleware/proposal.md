## Why

CodexCont provides Responses-compatible continuation folding for a specific
reasoning-truncation failure mode observed in Codex streaming traffic. Importing
it into this repository makes the implementation, fixtures, and agent-facing
installation runbook available, and wiring it into codex-lb makes the behavior
passive for normal Responses-compatible HTTP streams.

## What Changes

- Add the standalone `middleware/` Starlette proxy, `run.py` entrypoint, and
  `config.example.toml` sample configuration.
- Add a codex-lb-native continuation fold in the core Responses stream client,
  enabled by default through `CODEX_LB_CODEX_CONTINUATION_ENABLED`.
- Reuse the selected codex-lb upstream account, auth headers, route, session,
  and retry surface for hidden continuation rounds instead of creating separate
  user-visible service requests.
- Bypass the HTTP session bridge for continuation-eligible HTTP streams when
  `CODEX_LB_CODEX_CONTINUATION_BYPASS_HTTP_BRIDGE=true`, so the bridge cannot
  skip continuation folding.
- Add the self-contained CodexCont fixture tests and agent installation guide.
- Preserve the existing codex-lb README as the project landing page while
  documenting CodexCont in `CODEXCONT.md`.
- Package the new `middleware` module and direct runtime dependencies alongside
  the existing project metadata.

## Impact

- Adds a default-on passive continuation mechanism to codex-lb's HTTP Responses
  stream path.
- Adds an optional standalone Responses proxy surface at configured listen paths,
  defaulting to `/v1/responses` on `127.0.0.1:8787` when run via `run.py`.
- Continuation-eligible streams buffer tentative final output until terminal
  usage determines whether another hidden round is required.
- Hidden continuation rounds may increase upstream token usage; reconstructed
  response metadata reports folded rounds and billed usage.
- Adds security-sensitive credential-forwarding behavior that is covered by
  offline tests and normative OpenSpec requirements.
