## ADDED Requirements

### Requirement: HTTP-bridge reconnect types a required owner as continuity provenance

HTTP-bridge reconnect MUST type a required preferred account as a continuity
owner. A live file pin, `require_preferred_account`, or account-neutral
recovery populates that required owner. If selection then returns typed
`continuity_owner_unavailable`, reconnect MUST fail closed immediately with
the existing required-owner unavailable error and MUST NOT wait for generic
selection recovery. A soft `1011` reconnect with no required owner MUST NOT
mark the preferred account as a continuity owner.

#### Scenario: Soft 1011 file-pin reconnect types the pin account as continuity owner

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **WHEN** the proxy reconnects that session
- **THEN** account selection MUST receive `account_a` as the preferred account
- **AND** it MUST mark that preferred account as a continuity owner
- **AND** it MUST request a single-account routing override for that file pin
- **AND** it MUST NOT enable preferred-account fallback to another account

#### Scenario: Soft 1011 reconnect without a required owner remains untyped

- **GIVEN** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the still-unsubmitted request has no live file pin and no other required owner
- **WHEN** the proxy reconnects that session
- **THEN** account selection MUST NOT mark a preferred account as a continuity owner
- **AND** it MUST NOT request a single-account routing override
- **AND** it MAY exclude `account_a` and choose another eligible account

#### Scenario: Required-owner reconnect maps typed continuity_owner_unavailable immediately

- **GIVEN** HTTP-bridge reconnect has a required preferred account
- **AND** account selection returns typed `continuity_owner_unavailable`
- **WHEN** the proxy reconnects that session
- **THEN** the proxy MUST fail closed with the existing required-owner unavailable error
- **AND** it MUST NOT wait for generic account-selection recovery before returning that envelope
