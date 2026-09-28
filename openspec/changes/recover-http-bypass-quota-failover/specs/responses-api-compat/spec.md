## ADDED Requirements

### Requirement: Verified full-history HTTP fallback releases turn ownership only on quota evidence

When HTTP streaming bypasses the bridge, the first attempt MUST go to the required turn-state owner with every downstream session and turn-state alias intact. A healthy owner MUST see unchanged routing and headers, and the service MUST NOT run the durable full-resend verification for it.

The owner MAY be released only on quota evidence: a quota or rate-limit rejection from the owner before any downstream-visible output, in either HTTP status or `response.failed` frame form, or a selection-time owner loss whose persisted owner status is `RATE_LIMITED` or `QUOTA_EXCEEDED`. Release additionally requires all of these: durable bridge metadata in the same API-key scope proves the request is a complete full resend; the proof's owner equals the required turn-state owner; the request has no explicit `previous_response_id` and no input-file owner; the request is not a forwarded owner request; and the routing strategy is not `single_account`.

On release, the service MUST exclude the owner, clear the turn-state requirement, and dispatch the same account-neutral projection the HTTP bridge uses for a verified full resend. It MUST strip every downstream session and turn-state alias from the replacement dispatch only. If the projection is not account-neutral, the owner MUST remain required.

Authentication failures, server errors, confirmed pre-dispatch transport failures, non-quota selection-time owner loss, downstream-visible output, a missing or unavailable proof, and a proof for another owner MUST leave the request owner-bound.

#### Scenario: Healthy owner keeps its affinity

- **WHEN** a verified account-neutral full resend bypasses the bridge
- **AND** the owner completes the request
- **THEN** only the owner is dispatched, with the downstream session and turn-state aliases
- **AND** no durable full-resend verification runs

#### Scenario: Quota rejection after verified HTTP bypass

- **WHEN** a verified full resend bypasses the bridge
- **AND** the owner rejects it with HTTP 429 `usage_limit_reached` before output
- **THEN** another eligible account completes the request with the account-neutral projection
- **AND** only the replacement dispatch has the session and turn-state aliases removed

#### Scenario: Quota rejection frame is equivalent

- **WHEN** the owner sends a `response.failed` frame with `usage_limit_reached` before output
- **THEN** the request moves exactly as it does for the HTTP 429

#### Scenario: Owner-bound reasoning is projected away

- **WHEN** the verified full resend carries encrypted reasoning and upstream item ids
- **AND** the owner rejects it with a quota error before output
- **THEN** the replacement account receives neither the reasoning items nor the item ids

#### Scenario: Owner already benched for quota

- **WHEN** the owner is `RATE_LIMITED` or `QUOTA_EXCEEDED` before the request
- **THEN** the owner is not dispatched and another eligible account completes the request without the aliases

#### Scenario: Non-quota owner loss stays owner-bound

- **WHEN** the owner is paused, or rejects with 401, a 5xx error, or a confirmed pre-dispatch transport failure
- **THEN** no other account is dispatched

#### Scenario: Visible output ends eligibility

- **WHEN** the owner emits output and then fails with a quota rejection
- **THEN** the failure is surfaced and no other account is dispatched

#### Scenario: Released owner is not dispatched again

- **WHEN** the owner and a first replacement both reject with quota errors before output
- **THEN** a third account completes the request and the owner is dispatched once

#### Scenario: Incomplete history or unavailable proof retains ownership

- **WHEN** a bypass request cannot prove full history, or the durable lookup fails
- **THEN** the owner's pre-visible quota rejection is surfaced
- **AND** no other account is dispatched

#### Scenario: Explicit ownership retains ownership

- **WHEN** a bypass request references previous_response_id, a file owner, or an account-owned item
- **THEN** the request retains its required account
