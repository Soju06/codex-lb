## Why

Graceful shutdown currently excludes WebSocket scopes from admission and in-flight tracking. A pod can therefore accept new WebSocket work after drain begins or terminate while an active turn is still writing terminal logs and settlements.

## What Changes

- Establish one monotonic drain deadline at preStop or the first process-shutdown signal and never extend or reopen it after shutdown commits.
- Reject every new WebSocket scope after the barrier, while tracking only admitted Responses WebSocket scopes through their turn-aware handler exit.
- Let admitted Responses connections finish already active turns through terminal delivery, request-log persistence, and API-key settlement; reject late turns and promptly close idle connections.
- Put the barrier before Uvicorn closes connections on every shipped or documented server launch path, preserve CLI startup/interrupt semantics, and keep the embedded metrics server from taking process-signal ownership.
- Declare the Uvicorn 0.47 API floor used by the owned launcher.
- Make Helm preStop wait on routing dwell plus `in_flight`, bounded by the same application deadline, with render-time timing guards.
- Add focused admission, cancellation, persistence, Helm, and real SIGTERM/SIGINT lifecycle regression coverage.

## Capabilities

### New Capabilities

- `graceful-shutdown`: Defines bounded HTTP and WebSocket admission and in-flight behavior during process drain.

### Modified Capabilities

- `deployment-installation`: Defines Helm preStop coordination, timing guards, and the shipped launch paths that must use the pre-connection drain server.
- `replica-operations`: Replaces the obsolete WebSocket shutdown limitation with the bounded replica-drain lifecycle.

## Impact

This affects process shutdown state, the project-owned Uvicorn launcher, shipped and documented server start commands, the outer in-flight ASGI middleware, Responses WebSocket lifecycle handling, Helm preStop and timing documentation, OpenSpec deployment contracts, package metadata, and focused lifecycle tests. It adds no new package, setting, database schema, or data migration; it declares the already locked Uvicorn 0.47 minimum and the Helm values schema only types existing timing values. The new render-time timing guard is an upgrade compatibility boundary: a retained `terminationGracePeriodSeconds` below `config.shutdownDrainTimeoutSeconds + 32` must be raised explicitly, while adopting the new default requires an intentional non-reuse or `--reset-values` upgrade with the key absent.
