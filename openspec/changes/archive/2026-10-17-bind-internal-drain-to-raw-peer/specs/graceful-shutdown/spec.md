## ADDED Requirements

### Requirement: Internal drain control authorizes the raw socket peer

The unauthenticated `/internal/drain/start`, `/internal/drain/stop`, and `/internal/drain/status` endpoints MUST authorize the caller against the launcher-preserved raw socket peer. A forwarded-client projection MUST NOT satisfy that authorization, regardless of which peers `FORWARDED_ALLOW_IPS` trusts. A request whose raw peer is not a loopback address MUST be rejected with 403, and MUST NOT begin drain, commit the shutdown barrier, or report drain state. If raw-peer capture is unavailable the request MUST be rejected on the same terms. A loopback raw peer MUST remain authorized even when the request also carries a forwarded client address.

#### Scenario: Spoofed loopback from a remote peer is rejected

- **WHEN** forwarding projection trusts every peer
- **AND** a request from a remote transport peer supplies a loopback forwarded client address
- **AND** it posts to the drain-start endpoint with a drain-deadline header
- **THEN** the response is 403
- **AND** the process is not draining
- **AND** the one-way shutdown barrier is not committed

#### Scenario: Spoofed loopback cannot read or stop drain

- **WHEN** forwarding projection trusts every peer
- **AND** a request from a remote transport peer supplies a loopback forwarded client address
- **THEN** the drain-stop endpoint responds 403
- **AND** the drain-status endpoint responds 403

#### Scenario: Loopback caller stays authorized when a forwarded address is present

- **WHEN** forwarding projection trusts every peer
- **AND** a loopback caller requests drain status with a remote forwarded client address
- **THEN** the request is authorized
- **AND** the response reports current drain state

#### Scenario: Missing raw-peer capture fails closed

- **WHEN** an internal drain endpoint receives a request without preserved raw socket provenance
- **THEN** the response is 403
