## ADDED Requirements

### Requirement: The metrics server does not reconfigure process logging

When metrics are enabled, starting the in-process Prometheus metrics server
MUST NOT change the level, handlers, or formatters of any logger configured
by the server command. Main-server access lines MUST keep being written with
codex-lb's redacting formatters after the metrics server starts. Requests to
the metrics endpoint MAY appear in the same access log.

#### Scenario: Access logging survives metrics startup

- **GIVEN** metrics are enabled
- **WHEN** a client requests `GET /health` after the metrics server has started
- **THEN** the access line for that request is written
- **AND** credentials in the request target are redacted in that line

#### Scenario: Metrics scrapes are access-logged

- **GIVEN** metrics are enabled
- **WHEN** a client requests `GET /metrics` on the metrics port
- **THEN** the access log may contain a line for that request
