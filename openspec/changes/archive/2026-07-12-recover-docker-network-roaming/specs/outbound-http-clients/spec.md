## ADDED Requirements

### Requirement: Process-wide network failures rotate shared transport state

The service MUST classify local DNS resolver and host-route failures separately from account-specific upstream failures. When such a failure affects the current shared outbound HTTP client, the service MUST make subsequent callers use a replacement client while preserving active leases on the retired client. Concurrent failures from the same retired generation MUST NOT cause repeated client rotations.

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

### Requirement: Process-wide network failures are account neutral

The proxy MUST NOT record a transient, permanent, quota, or rate-limit health failure against an account when an attempt fails because the local process cannot resolve or route to the upstream host.

#### Scenario: Wi-Fi transition does not poison account health

- **WHEN** an upstream attempt fails with a classified local DNS or host-route failure
- **THEN** the selected account's health counters and cooldown state are unchanged
- **AND** continuity ownership remains pinned to that account
