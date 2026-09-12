## ADDED Requirements

### Requirement: Revoked access-token errors require reauthentication

The upstream error code `token_revoked` MUST be classified as a permanent
reauthentication failure equivalent to `token_invalidated`. Account-health
handling MUST mark the selected account `reauth_required`, while movable
pre-visible work MAY fail over according to its surface-specific retry contract.

#### Scenario: Revoked token marks reauthentication required

- **WHEN** upstream rejects an account with error code `token_revoked`
- **THEN** account health marks that account `reauth_required`
- **AND** the error retains HTTP status 401 where surfaced
