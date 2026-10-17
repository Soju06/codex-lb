## Context

The maintainer confirmed #2149 on main. The request-finalization sweep grants each detached session a fresh five-second lock wait. ERR #1905 owns separate admission guards and has cleared this scope.

## Goals / Non-Goals

Bound aggregate lock waiting during request finalization. Preserve handoff release, session tracking, cancellation and lifecycle cleanup. Socket-close completion and registry-lock waiting retain their existing contracts.

## Decisions

Use the service's monotonic clock and one deadline before the detached-session loop. Each attempt receives the remaining time; stop with a skipped-count warning when none remains. This avoids cancelling a retirement owner midway through resource cleanup. Keep the existing retirement interface and sequential order.

The accepted #2149 helper regression seam proves exact virtual elapsed time and deferred cleanup. The public response stream proves finalization uses that deadline during completion and cancellation.

## Risks / Trade-offs

A busy first session can defer later ready sessions. They remain tracked for later sweeps and lifecycle owners. Existing bounded socket-close work may outlast the lock-wait budget; this change does not impose cancellation on resource owners.
