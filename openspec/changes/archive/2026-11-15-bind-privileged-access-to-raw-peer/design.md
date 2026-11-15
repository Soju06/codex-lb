## Context and decision

`TrustedProxyHeadersMiddleware` is already outermost. It records the server-observed peer before applying Uvicorn's configured projection to `scope["client"]` and scheme. `raw_socket_peer_host` returns that preserved host and fails closed when capture is absent. Reuse this boundary rather than reconstructing provenance from mutable forwarded fields.

The dashboard sanitizer and authentication resolver must both check the raw peer: retaining a forged identity field at one boundary must not be enough to authenticate at the other. Existing identity cardinality, group parsing, roles and password fallback remain unchanged. Projected clients and schemes remain available for attribution.

Drain's existing loopback-only policy stays intact, including its current localhost/IP classification. One shared guard runs before deadline validation, shutdown mutation or bridge callbacks on all three routes. A request through a local reverse proxy still has a loopback transport peer; this change does not add a separate policy for requests that proxy forwards to drain.

## Reproduction and verification design

With projection trust `*`, a raw remote peer can currently project a loopback/trusted address, retain an asserted identity and receive drain status. A trusted loopback proxy projecting a public client is rejected. Exercise actual application middleware and protected HTTP routes for IPv4/IPv6; cover absent and empty captured provenance independently. Check header scrubbing and actor resolution as well as status codes, preserve projected attribution, and prove denied drain calls cannot parse a malformed deadline, alter shutdown state or invoke bridge status/mark callbacks.

Source observations and focused reference diffs: https://github.com/Soju06/codex-lb/pull/2632 and https://github.com/Soju06/codex-lb/pull/2633. Their review summaries are supporting context; local source checks and reproductions independently confirm the defects.
