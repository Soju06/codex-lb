## ADDED Requirements

### Requirement: HTTP-bridge reconnect distinguishes deleted file owners from transient misses

HTTP-bridge reconnect MUST mark a live required file-pin owner as continuity
provenance. It MUST map a selection miss immediately to the existing 502
`previous_response_owner_unavailable` response only when selection confirms
that the required owner account no longer exists. A transient required-owner
saturation MUST retain bounded recovery within the reconnect deadline and MUST
retry the same owner. If recovery does not succeed, reconnect MUST retain the
existing terminal fail-closed response.

#### Scenario: Deleted required file owner maps immediately

- **GIVEN** a reconnect request has a live file pin to `account_a`
- **AND** `account_a` no longer exists in the runtime account catalog
- **WHEN** continuity-owner selection confirms that disappearance
- **THEN** reconnect MUST return 502 `previous_response_owner_unavailable`
- **AND** it MUST NOT wait for generic account-selection recovery

#### Scenario: Transient required file-owner saturation recovers

- **GIVEN** a reconnect request has a live file pin to `account_a`
- **AND** selection reports that `account_a` is transiently saturated
- **WHEN** the existing bounded recovery wait permits another attempt
- **THEN** reconnect MUST wait within its existing deadline
- **AND** it MUST retry `account_a` without enabling fallback
- **AND** it MUST continue successfully if `account_a` recovers

#### Scenario: Terminal transient miss remains fail-closed

- **GIVEN** reconnect has any owner required to be preferred
- **AND** bounded recovery cannot select that owner before termination
- **WHEN** reconnect returns the terminal selection failure
- **THEN** it MUST return the existing 502 `previous_response_owner_unavailable`
