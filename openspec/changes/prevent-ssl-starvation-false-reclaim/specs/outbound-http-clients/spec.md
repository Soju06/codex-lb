## ADDED Requirements

### Requirement: Outbound TLS trust material is loaded once per process

Loading the certificate authority bundle is a synchronous read of every root
certificate. The service MUST build at most one outbound SSL context per worker
process and reuse that exact instance for every project-owned aiohttp and
aiohttp-socks connector, so no upstream request pays that read on the event
loop.

During normal application startup the process MUST construct that context
before it begins serving requests. The shared HTTP connector, the shared
WebSocket connector, Codex direct sessions, Codex SOCKS sessions, and the
settings upstream-proxy probe MUST all receive that same instance. Runtime code
MUST NOT call the uncached constructor directly, and MUST NOT mutate the
published context's verification mode, hostname checking, certificate
authority locations, ciphers, or ALPN configuration.

Because the context is fixed for the life of the process, a change to the
system or bundled trust roots takes effect only after the process restarts.

#### Scenario: Connector generations reuse one context

- **GIVEN** the shared HTTP client has been initialized
- **WHEN** the client is later rotated and its connectors are rebuilt
- **THEN** the certificate authority bundle is read only once for the process
- **AND** every connector across both generations receives the same context instance

#### Scenario: Codex and proxy-probe factories reuse the same context

- **WHEN** a Codex session, a Codex SOCKS connector, or the settings SOCKS proxy probe builds its connector
- **THEN** it receives the process's shared outbound SSL context rather than constructing its own
