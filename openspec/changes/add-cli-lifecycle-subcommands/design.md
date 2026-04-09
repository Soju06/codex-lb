## Overview

This change adds a small command-line lifecycle layer around the existing uvicorn entrypoint without changing the default foreground execution path that containers and existing operators already rely on.

## Decisions

### Preserve the existing bare-command behavior

`codex-lb-cinamon` without a subcommand remains equivalent to foreground `serve`. This avoids breaking existing shell usage and container entrypoints that invoke `python -m app.cli --host ... --port ...`.

### Add explicit lifecycle subcommands

The CLI will support:

- `serve`: explicit foreground startup
- `start`: detached background startup with readiness checks
- `status`: report whether the tracked background process is still running
- `shutdown`: stop the tracked background process and remove runtime metadata

### Track runtime state with a PID metadata file

Background lifecycle commands need a stable place to exchange state across invocations. The CLI will write a small JSON metadata file that records the PID, host, port, and log path. By default this file lives under the existing data directory so local installs and container-like layouts both have a predictable location.

### Use a readiness probe before reporting success

`start` should not immediately report success after spawning a child process. It will wait for `/health/live` to respond on the selected host/port before returning. If the child exits early or readiness never succeeds, `start` will fail and remove the PID file.

### Use signal-based shutdown

`shutdown` will use the recorded PID and send a termination signal to the tracked process. On Unix-like systems this gives uvicorn a normal signal path. On platforms where Python maps `SIGTERM` differently, this remains a best-effort local operator command rather than a replacement for a full service manager.
