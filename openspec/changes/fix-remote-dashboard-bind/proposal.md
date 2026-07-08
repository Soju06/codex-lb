## Why

Operators running `codex-lb` directly with `uvx` or the console script can end up with a dashboard that works only from localhost. That presents as `http://<server-ip>:2455/dashboard` timing out even though the process is running, while Docker deployments already bind the service to all interfaces.

## What Changes

- Make the CLI server bind default match the Docker image: `0.0.0.0:2455`.
- Add `CODEX_LB_HOST` as the project-specific environment override, while keeping `--host` as the highest-precedence explicit choice.
- Document how to force local-only binding and how to diagnose closed remote port access.

## Impact

- CLI startup behavior for `codex-lb` and `uvx codex-lb`.
- Operator docs and `.env.example`.
- Unit tests for CLI bind-address precedence.
