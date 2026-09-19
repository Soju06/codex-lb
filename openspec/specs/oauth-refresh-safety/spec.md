# OAuth Refresh Safety Specification

## Purpose

Define how OAuth credential refreshes preserve the newest valid token version and avoid false reauthentication states under concurrent work.

## Requirements

### Requirement: Permanent refresh failures use current credential state
Before recording a permanent OAuth refresh failure, the system SHALL reload the account's current credential state from the database rather than relying on a session-cached entity.

#### Scenario: Another refresh already stored newer credentials
- **WHEN** a refresh attempt receives a permanent provider error after another attempt stored different refresh-token material
- **THEN** the system SHALL keep the account routable and converge on the newer stored account state

#### Scenario: The current credential version is permanently invalid
- **WHEN** a refresh attempt receives a permanent provider error and a database reload confirms that the refresh-token material has not changed
- **THEN** the system SHALL mark that account as requiring reauthentication

### Requirement: Token rotation writes are conditional
The system SHALL persist a refreshed OAuth token set only when the stored refresh-token version still matches the version used to perform the provider exchange.

#### Scenario: Refresh result owns the current token version
- **WHEN** a provider exchange succeeds and the stored refresh-token material still matches the exchanged version
- **THEN** the system SHALL atomically store the refreshed token set

#### Scenario: A newer token version wins the write race
- **WHEN** a provider exchange succeeds but the stored refresh-token material changed before persistence
- **THEN** the system SHALL preserve the newer stored token set and return the current account state

### Requirement: Conditional write conflicts do not flush stale state
The system MUST NOT persist pending stale account mutations when a conditional token update loses its version comparison.

#### Scenario: Session-bound stale account loses compare-and-swap
- **WHEN** a conditional token update matches no row while the session contains pending mutations for the stale account entity
- **THEN** the system SHALL roll back those mutations before loading the current account state

### Requirement: Delayed refresh callers do not replay rotated tokens
The system SHALL reload stored credentials before a refresh exchange. If another caller already rotated the refresh token, the delayed caller SHALL use the stored credentials without exchanging the stale token again.

### Requirement: Proxy refresh failures preserve conditional auth decisions
The Messages proxy SHALL NOT repeat an unconditional account-status write after AuthManager has handled a permanent refresh failure. Detached refresh tasks SHALL own their database session so cancellation of a requesting client cannot interrupt token persistence.
