## ADDED Requirements

### Requirement: Process-wide network failures rotate shared transport state

The service MUST classify local DNS resolver and host-route failures separately from account-specific upstream failures. Classification MUST come from typed exception provenance or an already-preserved stable internal code, not from matching arbitrary upstream message text. When such a failure affects the current shared outbound HTTP client, the service MUST make subsequent callers use a replacement client while preserving active leases on the retired client. Concurrent failures from the same retired generation MUST NOT cause repeated client rotations. Replacement construction and cleanup MUST remain cancellation-safe: an interrupted or failed replacement MUST close partially created resources and leave the previous generation current.

#### Scenario: DNS failure rotates the current shared client once

- **WHEN** concurrent outbound operations using the same shared client fail with a local DNS resolution error
- **THEN** the shared client is replaced once
- **AND** subsequent operations lease the replacement client
- **AND** active users of the retired client retain their lease until release

#### Scenario: Failure from a retired client does not rotate its replacement

- **WHEN** one caller has already replaced the shared client after a process-wide network failure
- **AND** another caller from the retired client reports the same failure
- **THEN** the replacement client remains current
- **AND** no additional replacement is created for that retired generation

#### Scenario: Upstream message text does not manufacture local provenance

- **WHEN** a genuine upstream failure uses `upstream_unavailable` and a message such as `Network is unreachable`
- **AND** no typed local-network classification accompanies it
- **THEN** the failure does not enter process-network recovery

#### Scenario: Cancelled replacement preserves the live generation

- **WHEN** shared-client replacement is cancelled after creating only part of the replacement transport
- **THEN** all partially created sessions and connectors are closed
- **AND** the previously current client generation remains current

### Requirement: Process-wide network failures are account neutral

The proxy MUST NOT record a transient, permanent, quota, rate-limit, or circuit-breaker health failure against an account when an attempt fails because the local process cannot resolve or route to the upstream host. Routed proxy transport failures MUST retain a credential-safe machine-readable classification after the original exception message is sanitized. A permanent missing proxy hostname MUST remain an endpoint-scoped proxy failure rather than entering process-wide recovery.

#### Scenario: Wi-Fi transition does not poison account health

- **WHEN** an upstream attempt fails with a classified local DNS or host-route failure
- **THEN** the selected account's health counters and cooldown state are unchanged
- **AND** the selected account's circuit breaker is unchanged
- **AND** continuity ownership remains pinned to that account

#### Scenario: Routed transient DNS failure remains account neutral after sanitization

- **WHEN** an HTTP or WebSocket attempt through a resolved upstream proxy route fails with transient DNS or local route loss
- **THEN** the credential-safe routed error carries the process-network classification
- **AND** the selected account's health and circuit-breaker state are unchanged

#### Scenario: Missing proxy hostname remains endpoint scoped

- **WHEN** resolving a configured upstream proxy hostname fails with a permanent name-not-found result
- **THEN** the failure remains `upstream_unavailable`
- **AND** the proxy does not classify the host process as disconnected
