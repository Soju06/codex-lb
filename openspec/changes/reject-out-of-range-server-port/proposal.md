## Why

The server CLI currently accepts any integer listener port, so values outside the operating-system range can reach Uvicorn, start the ASGI lifespan, run migrations, and create runtime state before socket binding fails with an indirect platform error. The CLI should reject those values at its public input boundary before startup has side effects.

## What Changes

- Accept main listener ports only in the inclusive range `0..65535` from both `--port` and `PORT`.
- Reject non-integer or out-of-range values before Uvicorn is imported or the ASGI application starts, with an error that identifies `--port/PORT` and the supported range.
- Add CLI-entrypoint regression coverage for both input sources and the inclusive boundaries while preserving flag-over-environment precedence.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `runtime-portability`: defines the portable main-listener port range and fail-fast validation contract for `--port` and `PORT`.

## Impact

- Code: `app/cli.py` listener-port parsing only.
- Tests: `tests/unit/test_cli.py` exercises the real CLI `main()` seam for flag and environment forms.
- Operators: invalid listener-port configuration fails immediately with an actionable range error instead of after application startup; valid ports and precedence are unchanged.
- No settings-model, launcher, deployment-manifest, dashboard, upstream-proxy, migration, or data-layout changes.
