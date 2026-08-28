## ADDED Requirements

### Requirement: Owned launch paths preserve raw peer before proxy projection

Every project-owned launch path for the main application MUST disable server-level proxy-header projection. The outermost application middleware MUST preserve the incoming HTTP or WebSocket `scope["client"]` before applying Uvicorn-compatible proxy projection exactly once. Downstream consumers MUST continue to observe Uvicorn's projected client and scheme. Projection MUST use `FORWARDED_ALLOW_IPS` unchanged: unset MUST trust `127.0.0.1`, empty MUST trust no peer, `*` MUST trust every peer, and explicit hosts or networks MUST retain Uvicorn's parsing and trusted-chain behavior. The change MUST NOT introduce a new setting.

#### Scenario: Owned launchers disable early projection

- **WHEN** the main application starts through the project CLI, development Compose, or a shipped direct FastAPI/Uvicorn command
- **THEN** server-level proxy-header projection is disabled
- **AND** application capture and projection run exactly once

#### Scenario: HTTP and WebSocket preserve both identities

- **WHEN** a trusted peer sends valid `X-Forwarded-For` and `X-Forwarded-Proto` headers over HTTP or WebSocket
- **THEN** the raw transport peer remains preserved
- **AND** downstream handling observes Uvicorn's projected client and protocol-appropriate scheme

#### Scenario: Forwarded allowlist behavior is unchanged

- **WHEN** `FORWARDED_ALLOW_IPS` is unset, empty, `*`, or an explicit host/network list
- **THEN** proxy projection follows Uvicorn's existing trust semantics
