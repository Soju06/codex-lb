## Context

Internal drain routes use a loopback-only transport contract. The outer
middleware preserves the socket peer before projecting forwarded client
metadata, but these three routes previously inspected the projected address.

## Decisions

Use the existing raw_socket_peer_host helper in one shared guard before any
deadline parsing, state mutation or bridge calls. Reuse the existing loopback
classifier so address semantics remain unchanged. Missing capture denies access.

Preserve the standard health routes, response schemas, drain deadline handling
and local preStop calls. A local reverse proxy remains a loopback transport peer;
this patch does not create a separate authentication policy for such a proxy.

## Verification

Exercise all three routes through the real app middleware with IPv4 and IPv6
peers and forwarded client addresses. Assert denied requests do not mutate state
or invoke bridge callbacks. Exercise legitimate local start/status/stop and
existing preStop/deadline tests. Compare the new cases with unmodified main.
