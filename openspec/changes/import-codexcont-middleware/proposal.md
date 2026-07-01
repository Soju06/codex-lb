## Why

CodexCont provides a standalone Responses-compatible middleware for a specific
reasoning-truncation failure mode observed in Codex streaming traffic. Importing
it into this repository makes the implementation, fixtures, and agent-facing
installation runbook available without replacing the existing codex-lb service.

## What Changes

- Add the standalone `middleware/` Starlette proxy, `run.py` entrypoint, and
  `config.example.toml` sample configuration.
- Add the self-contained CodexCont fixture tests and agent installation guide.
- Preserve the existing codex-lb README as the project landing page while
  documenting CodexCont in `CODEXCONT.md`.
- Package the new `middleware` module and direct runtime dependencies alongside
  the existing project metadata.

## Impact

- Adds an optional standalone Responses proxy surface at configured listen paths,
  defaulting to `/v1/responses` on `127.0.0.1:8787` when run via `run.py`.
- Does not change the existing codex-lb FastAPI application routing.
- Adds security-sensitive credential-forwarding behavior that is covered by
  offline tests and normative OpenSpec requirements.
