## Context

Docker port publishing is independent of the application's callback-listener lifetime. The default mapping reserves 1455 even when the listener is idle. Issue #2076 concerns that host-level collision.

## Goals / Non-Goals

Free the host callback port for other local applications in stock bridge-network deployments. Do not change OAuth service code, the fixed redirect URI, dashboard rendering, or host-network deployments.

## Decisions

Propose removing the host publication from portable launch examples and both Compose files. Keep internal callback port 1455 unchanged. Document device-code setup and manually pasted callbacks, plus a loopback-only opt-in for a dedicated machine.

An optional coexistence recipe preserving current defaults is an alternative if the owner rejects this default change. Merely stopping the internal callback listener cannot free a Docker-published host port.

## Risks / Trade-offs

Automatic browser callback capture will no longer work out of the box. The existing Windows netsh helper targets server-side port 1455 and therefore will not work with the proposed default. Documentation states these limits explicitly; this draft needs the owner's product decision and P1 approval before it can be ready.

Manual callbacks can display a browser connection error before the URL is pasted. Device-code sign-in avoids that intermediate error. The proposal does not claim to preserve the previous one-click Browser path.

## Migration Plan

Existing containers retain their original published ports after image updates. Recreate them without the callback mapping, preserving their data volume. Rollback restores the mapping and its original port conflict.

## Open Questions

Does the owner accept this default change, or prefer an optional coexistence recipe? Is documentation sufficient for the Windows helper limitation, or should a separate dashboard change guide users?

## Example

A laptop runs the proxy on 2455 and uses device-code account setup. Port 1455 remains free for another local application's sign-in. A dedicated host can instead opt into automatic callbacks with a loopback mapping.
