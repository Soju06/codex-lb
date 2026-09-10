## ADDED Requirements

### Requirement: Quota continuity recovery complements native failover

For a pre-visible explicit quota rejection, the proxy MAY release request-local
dispatch affinity and replay a verified account-neutral full-history
continuation when quota continuity recovery is enabled. The proxy MUST retain
native failure classification, account selection, retry counts, and deadlines.
It MUST NOT add a separate retry budget or artificial delay. Disabling recovery
MUST NOT disable ordinary native failover for requests that are already movable.

The behavior SHALL cover streaming HTTP/WebSocket egress and the HTTP responses
bridge. Uploaded files, incomplete history, durable operations, accepted work,
and single-account ownership MUST remain fail-closed. A transferred continuation
MUST omit obsolete response anchors and account-scoped turn-state headers.
Stored response/file ownership and unrelated sticky mappings MUST remain intact.

#### Scenario: Native failover stays authoritative

- **GIVEN** a movable request and recovery disabled
- **WHEN** native policy permits a quota retry
- **THEN** recovery does not suppress that native retry
- **AND** recovery does not extend its retry count or add a delay

#### Scenario: Verified continuation leaves an exhausted owner

- **GIVEN** a rejected continuation with verified account-neutral full history
- **AND** recovery is enabled and no hard owner requires the original account
- **WHEN** the native retry path can select another eligible account
- **THEN** the full body is replayed without obsolete account-local anchors
- **AND** the existing retry budget and deadline remain authoritative

#### Scenario: Unsafe continuity is preserved

- **GIVEN** incomplete history, account-scoped files, or durable operation ownership
- **WHEN** a quota failure occurs
- **THEN** the request does not cross accounts

#### Scenario: Accepted work is not duplicated

- **GIVEN** upstream accepted the request or output is downstream-visible
- **WHEN** a quota terminal arrives
- **THEN** this recovery does not replay the request

#### Scenario: No replacement preserves the upstream quota terminal

- **GIVEN** a quota-rejected request with no eligible replacement
- **THEN** the original quota class and reset metadata remain available
- **AND** no additional recovery loop is started
