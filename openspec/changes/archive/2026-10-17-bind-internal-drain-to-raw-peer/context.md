The outermost application middleware preserves the transport peer and then
applies Uvicorn's forwarding projection exactly once, so two different client
identities are available downstream. `request.client` is the projected one and
exists for logging, rate accounting, and request attribution. Authorization of
the peer itself belongs to `raw_socket_peer_host`, which
`_is_proxy_unauthenticated_socket_peer_allowed` already uses and which
`app/core/request_locality.py` uses for locality.

The internal drain guard was written against `request.client.host`. That is
sound only while no projection occurs, which is the case at the default
`FORWARDED_ALLOW_IPS=127.0.0.1` with an off-host reverse proxy. Once an
operator widens that variable — `*` is explicitly supported — the projected
value becomes attacker-controlled, and these endpoints have no second gate:
the API firewall covers only `/v1` and `/backend-api/codex`, the SPA catch-all
does not shadow `/internal/`, and the health router carries no dependencies.

The shipped preStop helper is not affected. `LocalDrainClient.start_drain`
sends only the drain-deadline header and `get_status` sends none, so there is
no forwarded address for the projection to adopt and its transport peer stays
loopback whatever `FORWARDED_ALLOW_IPS` says. A loopback caller that did arrive
with a forwarded address would be projected away from loopback and refused, so
the requirement pins that case to keep this change from introducing the denial
it does not currently have.

Example: with `FORWARDED_ALLOW_IPS=*`, a request from 203.0.113.24 carrying
`X-Forwarded-For: 127.0.0.1` and `x-codex-lb-drain-deadline-monotonic: 1.0`
previously committed the one-way shutdown barrier. `stop_drain()` then answers
409, so the replica served 503 until it was restarted. It is now rejected with
403 before any shutdown state is touched.

The guard continues to authorize a local reverse proxy by its loopback transport
peer. Deployments must not expose internal routes through that proxy unless
intended; requiring a different trust policy is outside this narrow change.
