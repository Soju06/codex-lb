## ADDED Requirements

### Requirement: Owner-forwarded compact settlement failures fail closed

An HTTP-bridge owner MUST treat any persistence exception while finalizing or
releasing a forwarded compact API-key usage reservation as a failed settlement,
and MUST NOT swallow it.
The owner MUST log the persistence failure, MUST attempt to release the
reservation through a fresh repository context, and MUST surface a `502`
`usage_settlement_failed` server error regardless of whether that fail-safe
release succeeds. The settlement failure MUST carry trusted internal provenance
that is checked before compact upstream retry, failover, and account-health error
handling, so the compact request is not sent upstream again and the selected
account is not penalized for a local persistence failure. When the reservation
is still `reserved` when the fail-safe release begins and that release succeeds,
the reservation's final status MUST be `released`. This behavior MUST NOT add or
alter stale-reservation cleanup or WebSocket health handling.

#### Scenario: Forwarded compact finalization fails after upstream success

- **GIVEN** a signed owner-forwarded compact request whose API-key reservation is `reserved`
- **AND** the upstream compact succeeds but usage finalization raises a persistence exception
- **WHEN** the owner handles the settlement failure
- **THEN** the owner attempts a fail-safe reservation release through a fresh repository context
- **AND** the request returns `502` with error code `usage_settlement_failed`
- **AND** the upstream compact is called exactly once and no account-health error is recorded
- **AND** when the reservation is still `reserved` at fail-safe release and that release succeeds, its status is `released`
