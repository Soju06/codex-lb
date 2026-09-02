## ADDED Requirements

### Requirement: Outbound TLS trust material is loaded once per process

Loading the certificate authority bundle is a synchronous read of every root
certificate. The service MUST build at most one outbound SSL context per worker
process from the bundled trust roots, so no request pays that read on the event
loop.

During normal application startup the process MUST construct that context
before it begins serving requests. The shared HTTP connector, the shared
WebSocket connector, Codex direct sessions, Codex SOCKS sessions, and both the
SOCKS and HTTP(S) forms of the settings upstream-proxy probe MUST all receive
that same instance. Runtime code MUST NOT call the uncached constructor
directly, and MUST NOT mutate the published context's verification mode,
hostname checking, certificate authority locations, ciphers, or ALPN
configuration.

Short-lived `aiohttp.ClientSession` instances created without an explicit
connector (the telemetry sender, runtime service probes, the Codex version
probe, HTTP bridge forwarding, and the health probe) verify against aiohttp's
own process-wide default context, which aiohttp builds once at import. They are
outside this requirement: they MUST NOT construct a per-call context either,
but they are not required to receive the shared instance.

Because the context is fixed for the life of the process, a change to the
system or bundled trust roots takes effect only after the process restarts.

#### Scenario: Connector generations reuse one context

- **GIVEN** the shared HTTP client has been initialized
- **WHEN** the client is later rotated and its connectors are rebuilt
- **THEN** the certificate authority bundle is read only once for the process
- **AND** every connector across both generations receives the same context instance

#### Scenario: Codex and proxy-probe factories reuse the same context

- **WHEN** a Codex session, a Codex SOCKS connector, or either form of the settings upstream-proxy probe builds its client
- **THEN** it receives the process's shared outbound SSL context rather than constructing its own
