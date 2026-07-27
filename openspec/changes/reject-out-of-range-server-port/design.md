## Context

`app.cli._parse_server_port` currently converts the selected `--port` or `PORT` value with `int(...)` and returns every integer. `main()` then stores the value in `PORT` and calls `_load_uvicorn().run(...)`. Uvicorn starts the ASGI lifespan before binding the listener socket, so an out-of-range integer can trigger migrations and runtime-state creation before the operating system rejects the bind.

The supported socket range is `0..65535` inclusive. Port `0` is intentionally valid because Uvicorn uses it to request an ephemeral listener. The CLI flag already takes precedence over the environment through argparse and that behavior must remain unchanged.

## Goals / Non-Goals

**Goals:**

- Reject out-of-range integer listener ports at the existing CLI parser boundary.
- Preserve the current non-integer failure, valid boundary behavior, and flag-over-environment precedence.
- Prove through `main()` that invalid values never load Uvicorn and valid boundaries are forwarded unchanged.

**Non-Goals:**

- Refactor settings or argument parsing.
- Change Uvicorn startup or lifespan ordering.
- Change deployment launchers, manifests, dashboard settings, or upstream-proxy endpoint validation.
- Normalize whitespace/sign syntax or add retries, telemetry, or new configuration.

## Decisions

### Validate the converted integer in `_parse_server_port`

After the existing `int(...)` conversion, `_parse_server_port` checks `0 <= port <= 65535` and raises `SystemExit` otherwise. The error names the shared `--port/PORT` input surface, includes the invalid value, and states the inclusive supported range.

This keeps validation before `os.environ["PORT"]`, `_load_uvicorn`, ASGI imports, lifespan work, and socket binding. Moving validation into settings or relying on Uvicorn/the operating system was rejected because both occur too late and allow startup side effects. Adding an argparse `type=` callback was rejected because the existing helper already owns server-only validation after non-server subcommands return.

### Preserve port zero and existing precedence

Port `0` remains valid and is forwarded unchanged for Uvicorn's ephemeral-listener behavior; `65535` is the upper inclusive boundary. `_parse_args` remains unchanged, so an explicit `--port` continues to override `PORT`.

### Test through the CLI `main()` seam

Parameterized unit tests invoke `cli.main(argv)` for both flag and environment forms. Invalid values replace `_load_uvicorn` with a fail-if-called stub and assert the actionable error. Boundary values replace it with a capturing fake and assert the exact forwarded `port`. This exercises the externally affected CLI path rather than testing the helper alone.

## Risks / Trade-offs

- Port `0` may surprise operators who expect only numbered listeners, but rejecting it would break existing Uvicorn-compatible behavior and is outside this bug fix.
- The combined `--port/PORT` label does not distinguish which source won; keeping it preserves the established integer-error source label and avoids threading source metadata through argparse for one validation branch.
- Socket availability and permissions remain runtime concerns; this check only rejects values that are never valid TCP/UDP port numbers.
