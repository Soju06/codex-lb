## ADDED Requirements

### Requirement: CCodex native fingerprint recognition
For upstream fingerprint preservation, the proxy MUST classify the exact originators `ccodex-internal` and `ccodex-handoff-worker` as native Codex. It MUST also classify User-Agent prefixes `ccodex-internal/` and `ccodex-handoff-worker/` case-insensitively as native Codex. HTTP and WebSocket upstream fingerprint paths MUST preserve these identities.

#### Scenario: HTTP preserves an allowlisted CCodex originator
- **WHEN** an HTTP Responses request has `originator: ccodex-internal`
- **THEN** the proxy preserves the supplied User-Agent and originator on the upstream request

#### Scenario: WebSocket preserves an allowlisted CCodex User-Agent
- **WHEN** a WebSocket Responses request has a User-Agent beginning with `ccodex-handoff-worker/`
- **THEN** the proxy preserves the supplied User-Agent on the upstream request

#### Scenario: Unlisted CCodex-like fingerprints are normalized
- **WHEN** a request has `originator: ccodex-unlisted` or a User-Agent beginning with `ccodex-unlisted/`
- **THEN** the proxy applies the existing non-native fingerprint normalization

### Requirement: Unlisted CCodex-like fingerprints remain non-native
The proxy MUST continue applying non-native fingerprint normalization to CCodex-like identifiers outside the explicit native fingerprint allowlist.

#### Scenario: Generic CCodex User-Agent is normalized
- **WHEN** a request has `User-Agent: ccodex/1.0`
- **THEN** the proxy applies the existing non-native fingerprint normalization

### Requirement: CCodex fingerprint recognition does not change transport selection
The CCodex fingerprint allowlist MUST NOT change the originator allowlist used for upstream transport selection.

#### Scenario: CCodex fingerprint recognition does not change transport selection
- **WHEN** a request uses `originator: ccodex-internal` or `originator: ccodex-handoff-worker`
- **THEN** the proxy does not select the WebSocket transport based only on that originator
