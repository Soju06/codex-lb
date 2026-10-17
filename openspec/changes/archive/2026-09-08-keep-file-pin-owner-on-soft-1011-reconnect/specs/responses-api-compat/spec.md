## ADDED Requirements

### Requirement: Soft HTTP-bridge 1011 reconnect keeps a live file-pin owner

A still-unsubmitted HTTP-bridge reconnect MUST keep a live `input_file.file_id`
pin as a required owner after a soft session closes with `1011`.
When an HTTP-bridge session is soft (prompt-cache or request locality) and
upstream closed it with `1011`, a still-unsubmitted request that carries a
live `input_file.file_id` pin MUST keep that pin account as a required
reconnect owner. The proxy MUST NOT exclude that account solely because the
close code was `1011`, and MUST NOT fall back to another account while the
pin is live. If the required pin account is already excluded or cannot be
reconnected, the proxy MUST fail closed with the existing required-owner
unavailable error. A soft `1011` reconnect that has no live file pin and no
other required owner MAY still skip the closed account.

#### Scenario: Soft 1011 reconnect keeps the file-pin account required

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **WHEN** the proxy reconnects that session
- **THEN** account selection MUST treat `account_a` as the required owner
- **AND** it MUST NOT add `account_a` to the excluded-account set solely because of `1011`
- **AND** it MUST NOT enable preferred-account fallback to another account

#### Scenario: Soft 1011 reconnect without a file pin may skip the closed account

- **GIVEN** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the still-unsubmitted request has no live file pin and no other required owner
- **WHEN** the proxy reconnects that session
- **THEN** account selection MAY exclude `account_a` and choose another eligible account

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be selected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection cannot return `account_a`
- **WHEN** the proxy reconnects that session
- **THEN** the proxy MUST fail closed with the existing required-owner unavailable error
- **AND** it MUST NOT replace that envelope with a generic selection failure

#### Scenario: Soft 1011 file-pin reconnect fails closed when the required owner cannot be connected

- **GIVEN** a live in-memory pin `file_xyz -> account_a`
- **AND** a soft prompt-cache HTTP-bridge session on `account_a` closed with `1011`
- **AND** the next still-unsubmitted `/v1/responses` request references `file_xyz`
- **AND** account selection returns `account_a`
- **AND** opening a replacement upstream for `account_a` fails
- **WHEN** the proxy reconnects that session on submit
- **THEN** the client-visible error MUST be the existing required-owner unavailable error
- **AND** it MUST NOT be replaced with a generic `upstream_unavailable` envelope
