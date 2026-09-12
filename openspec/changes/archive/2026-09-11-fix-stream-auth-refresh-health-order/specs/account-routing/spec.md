## MODIFIED Requirements

### Requirement: Revoked access-token errors require reauthentication

The upstream error code `token_revoked` MUST be classified as a permanent
reauthentication failure equivalent to `token_invalidated`. Account-health
handling MUST mark the selected account `reauth_required`, while movable
pre-visible work MAY fail over according to its surface-specific retry contract.
An initial pre-visible streaming HTTP 401 MUST reach the existing forced-refresh
handler before account health is written. A repeated revoked-token failure after
successful forced refresh MUST exclude that account from movable work, and
API-key usage reservations MUST settle before the account-health write.

#### Scenario: Revoked token marks reauthentication required

- **WHEN** upstream rejects an account with error code `token_revoked`
- **THEN** account health marks that account `reauth_required`
- **AND** the error retains HTTP status 401 where surfaced

#### Scenario: Refreshed revoked-token stream settles before account health

- **GIVEN** a streaming Responses request with an API-key usage reservation
- **AND** the selected account returns HTTP 401 with `token_revoked`
- **WHEN** forced refresh succeeds and the same-account retry returns `token_revoked` again
- **THEN** another eligible account completes the movable request
- **AND** the revoked account is excluded from further attempts
- **AND** the reservation is committed before the revoked account is marked `reauth_required`
