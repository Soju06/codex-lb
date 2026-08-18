## ADDED Requirements

### Requirement: Direct-egress upstream websockets do not offer permessage-deflate
When codex-lb opens a direct-egress upstream websocket (the Responses websocket or the
realtime live sideband over the `websockets` transport, used when no upstream proxy route
applies), it MUST NOT offer the `permessage-deflate` extension in the upstream handshake,
matching the routed and raw-handshake upstream transports, which already run uncompressed.
This requirement applies only to the proxy-to-upstream link: the server MUST continue to
negotiate `permessage-deflate` on the client-facing websocket, as required by the
downstream websocket ingress requirement.

#### Scenario: Direct upstream handshake omits the compression extension offer

- **WHEN** codex-lb connects an upstream websocket via the direct-egress `websockets` transport
- **THEN** the handshake does not offer `permessage-deflate` (the transport is invoked with compression disabled)
- **AND** persona headers, subprotocols, open-timeout, ping-timeout, message-size cap, and proxy resolution are unchanged

#### Scenario: Client-facing compression negotiation is unchanged

- **WHEN** a client connects to a Responses websocket route offering `permessage-deflate`
- **THEN** the server still negotiates `permessage-deflate` on the client-facing socket
- **AND** the downstream ingress budget continues to apply to the decompressed message size
