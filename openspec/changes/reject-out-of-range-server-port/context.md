# Context: reject-out-of-range-server-port

## Purpose and scope

This change closes a server-startup foot-gun at the public CLI boundary. Its normative contract lives in the `runtime-portability` delta spec in this change. The implementation is intentionally limited to listener-port parsing plus CLI-entrypoint regressions; settings, launchers, deployment manifests, the dashboard, and upstream-proxy endpoint fields are separate surfaces.

## Decision rationale

Uvicorn starts the ASGI lifespan before it binds the listener socket. Letting the socket layer reject an impossible port is therefore too late: migrations and runtime-state creation can already have occurred, and the final platform-specific bind error does not identify the bad `--port` or `PORT` input. Validating in the existing `_parse_server_port` helper is the earliest server-only seam and avoids a second configuration path.

The accepted range is `0..65535` inclusive. The upper bound is the operating-system port limit. The lower bound remains `0`, rather than `1`, because Uvicorn deliberately supports port zero as a request for an ephemeral listener and codex-lb already forwards it.

## Constraints and non-goals

- Preserve argparse's existing explicit-flag precedence over the environment.
- Preserve the current integer syntax accepted by Python's `int(...)`; this change adds a range bound rather than normalization rules.
- Fail before Uvicorn import, ASGI lifespan, migrations, secret generation, or data-directory writes.
- Do not add a setting, fallback name, launcher change, UI control, retry, or telemetry.

## Failure modes and boundaries

Values such as `-1`, `65536`, and `70000` are syntactically integers but can never be valid listener ports; they should produce the same deterministic range error on every platform. Non-integer values retain an integer-specific error that also identifies the supported range. Ports can still fail later because they are occupied or require permissions; those are real runtime conditions and are not masked by this preflight check.

## Example

With `PORT=70000`, the command exits before Uvicorn is loaded and reports that `--port/PORT` supports `0..65535`. With `--port 0` or `PORT=65535`, the selected integer is forwarded unchanged to Uvicorn.

## Related contracts

- Main capability: `openspec/specs/runtime-portability/spec.md`
- Change delta: `openspec/changes/reject-out-of-range-server-port/specs/runtime-portability/spec.md`
