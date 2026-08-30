## ADDED Requirements

### Requirement: Verified same-owner stale-anchor replacement remains owner-bound

When an HTTP-bridge continuation has a verified, prefix-safe full resend that
is not account-neutral because its retained tool or file context is owner-bound,
and the continuity owner explicitly rejects `previous_response_id` before
producing any response output, the proxy MUST remove only that rejected anchor
and attempt the bounded unanchored replacement on the proven owner account.
If a preferred owner account is available in this recovery path, admission MUST
set `fallback_on_preferred_account_unavailable` to false. If that owner cannot
accept the replacement, the proxy MUST fail closed rather than selecting an
alternate account. The replacement MUST retain the existing operation fence,
settlement, one-shot replay, and retry-circuit rules.

#### Scenario: Owner-bound replacement stays on the rejecting owner

- **GIVEN** a verified full resend contains retained owner-bound tool history
- **AND** upstream explicitly rejects its `previous_response_id` before output
- **WHEN** the proxy prepares the one-shot unanchored replacement
- **THEN** the replacement omits the rejected `previous_response_id`
- **AND** it is admitted with the proven owner as `preferred_account_id`
- **AND** preferred-owner fallback is disabled
- **AND** the replacement does not migrate accounts

#### Scenario: Unavailable owner fails closed

- **GIVEN** the owner-bound replacement has a proven preferred account
- **AND** that account is unavailable or saturated during replacement admission
- **WHEN** the proxy selects a bridge session
- **THEN** the request fails with the existing retryable owner-unavailable result
- **AND** no alternate account receives the retained owner-bound context

#### Scenario: Account-neutral replay remains independent

- **GIVEN** an explicit stale-anchor rejection passes the existing account-neutral
  full-resend proof
- **WHEN** the proxy performs account-neutral recovery
- **THEN** its existing owner-exclusion and account-neutral selection behavior
  remains unchanged

#### Scenario: Other recovery paths remain unchanged

- **WHEN** a request is delta-only, prefix-unverified, transport-only, or has no
  explicit stale-anchor rejection
- **THEN** the proxy does not use this owner-bound replacement rule
- **AND** its existing fail-closed or anchored recovery behavior remains in
  force
