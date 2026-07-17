## ADDED Requirements

### Requirement: Owned server launch paths preserve the raw peer before proxy projection

Every server launch path shipped by the project MUST disable server-level proxy-header projection before loading the main application. The main application MUST preserve the incoming HTTP or WebSocket `scope["client"]` before delegating to Uvicorn's proxy-header middleware, and downstream consumers MUST continue to observe Uvicorn's projected client and scheme.

The application-level projection MUST use `FORWARDED_ALLOW_IPS` without reinterpretation. An unset value MUST use Uvicorn's `127.0.0.1` default, an empty value MUST trust no peer, `*` MUST trust every peer, and explicit hosts or networks MUST retain Uvicorn's parsing and trusted-chain behavior. No new operator setting SHALL be introduced for this projection.

#### Scenario: Project CLI disables server-level proxy projection

- **WHEN** the project CLI starts the main application with Uvicorn
- **THEN** it explicitly disables Uvicorn's server-level proxy-header middleware
- **AND** the application-level capture and projection middleware runs exactly once

#### Scenario: Direct shipped launch commands disable server-level proxy projection

- **WHEN** an operator uses a shipped direct FastAPI or Uvicorn launch command for the main application
- **THEN** the command includes the server's no-proxy-headers option

#### Scenario: Trusted HTTP peer retains projected behavior

- **WHEN** a trusted HTTP socket peer sends valid `X-Forwarded-For` and `X-Forwarded-Proto` headers
- **THEN** the application preserves the original socket peer before projection
- **AND** downstream request handling observes the client and scheme projected by Uvicorn

#### Scenario: Trusted WebSocket peer retains projected behavior

- **WHEN** a trusted WebSocket socket peer sends valid `X-Forwarded-For` and `X-Forwarded-Proto` headers
- **THEN** the application preserves the original socket peer before projection
- **AND** downstream WebSocket handling observes Uvicorn's projected client and `ws` or `wss` scheme

#### Scenario: Untrusted peer is not projected

- **WHEN** an HTTP or WebSocket socket peer is not trusted by `FORWARDED_ALLOW_IPS`
- **THEN** the preserved and downstream-visible clients both identify that socket peer
- **AND** forwarded headers do not change the downstream scheme

#### Scenario: Empty forwarded allowlist trusts no peer

- **WHEN** `FORWARDED_ALLOW_IPS` is explicitly empty
- **THEN** proxy headers from every socket peer leave the downstream client and scheme unchanged

#### Scenario: Wildcard forwarded allowlist retains Uvicorn semantics

- **WHEN** `FORWARDED_ALLOW_IPS` is `*`
- **THEN** every socket peer is eligible for Uvicorn's existing proxy-header projection
