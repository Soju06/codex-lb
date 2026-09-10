## ADDED Requirements

### Requirement: Ambiguous HTTP bridge operations converge after owner loss

The durable HTTP bridge operation ledger MUST preserve duplicate suppression
while an operation is live or ambiguous, but an `unknown` or `acknowledged`
operation MAY transition to the terminal `abandoned` state only after its
`updated_at` is older than `max(1800 seconds,
http_responses_session_bridge_request_budget_seconds)`, no local canonical or
detached bridge request is pending for that operation, and the durable owning
session has no owner or an owner lease that has remained expired for at least
one additional durable lease period.

An ownerless session produced by a graceful lease release MUST remain
ineligible until its recorded `lease_expires_at` has aged through that same
durable lease period. Candidate reads MUST lock the operation and session rows
on PostgreSQL on both the normal predicate path and the oversized-protection
bounded-page path.

The transition MUST atomically compare the operation state, `updated_at`,
durable event-spool progress, session owner instance, and owner epoch. A
concurrent recovery claim, owner renewal/takeover, or status proof MUST win
over abandonment. A persisted nonterminal event MUST advance durable
event-spool progress; if that event commits after candidate selection but
before the abandonment compare-and-set, the compare-and-set MUST affect zero
rows. The operation row and all event history MUST remain available for normal
retention. The proxy MUST NOT automatically resend or cancel the ambiguous
upstream operation.
The maintenance sweep MUST render no more than the repository's database-safe
number of protected operation IDs in one expanding predicate. If the local
protection snapshot exceeds that bound, the sweep MUST use bounded candidate
pages and filter the full protection set without truncating it; every protected
operation MUST remain unchanged while unrelated eligible operations MAY still
transition. Each oversized-protection sweep MUST inspect no more than a finite
scan budget, return a keyset cursor for the last inspected eligible row, and
the next maintenance sweep MUST resume after that cursor. Once the eligible
range is exhausted, the cursor MUST wrap to the beginning so later rows cannot
be starved by a protected prefix.

#### Scenario: stale ownerless operation is abandoned

- **GIVEN** an operation is `unknown` or `acknowledged`
- **AND** its `updated_at` is older than the bounded inactivity cutoff
- **AND** no canonical or detached local bridge request is pending for it
- **AND** its durable session owner is absent or its lease is expired
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation becomes terminal `abandoned`
- **AND** its operation row and event history remain intact
- **AND** no upstream request is dispatched by the sweep

#### Scenario: oversized protected prefix advances across sweeps

- **GIVEN** the local protection snapshot exceeds the database-safe bind limit
- **AND** more stale eligible rows are protected than one sweep's finite scan
  budget
- **AND** a later stale eligible operation is not protected
- **WHEN** the bridge maintenance sweep runs
- **THEN** it inspects no more than the finite scan budget in that sweep
- **AND** it preserves a keyset cursor after the inspected protected prefix
- **AND** a later sweep resumes after that cursor and may abandon the later
  unprotected operation
- **AND** the protected operations remain unchanged

#### Scenario: oversized protection keeps the session lock fence

- **GIVEN** the local protection snapshot requires the bounded-page path
- **WHEN** PostgreSQL selects an abandonment candidate
- **THEN** the candidate operation and owning session rows remain locked until
  the abandonment transaction commits
- **AND** a concurrent renewal or takeover cannot commit behind the stale
  candidate snapshot

#### Scenario: live owner is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session has an unexpired owner lease
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains `unknown` or `acknowledged`

#### Scenario: a brief owner-renewal lapse is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session owner lease expired less than one durable lease
  period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the original owner may still renew or finalize it

#### Scenario: a recent ownerless release is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session was released to ownerless less than one durable
  lease period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the releasing replica may finish its pending settlement

#### Scenario: pending local work is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** a canonical or detached local bridge generation still has a pending
  request state for that operation
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains unchanged

#### Scenario: concurrent recovery wins

- **GIVEN** a stale `unknown` operation is selected for abandonment
- **WHEN** a recovery claim changes it to `submitted` before the CAS commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `submitted`

#### Scenario: concurrent status proof wins

- **GIVEN** a stale `acknowledged` operation is selected for abandonment
- **WHEN** a nonterminal status event is durably appended before the
  abandonment compare-and-set commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `acknowledged`
- **AND** the appended event remains available in the operation history

#### Scenario: late status proof cannot revive abandonment

- **GIVEN** an operation has become `abandoned`
- **WHEN** a late upstream event or status callback attempts to update it
- **THEN** the write is rejected or becomes a no-op
- **AND** the operation remains `abandoned`

#### Scenario: abandoned continuation requests full-history recovery

- **GIVEN** operation admission finds an existing operation in `abandoned`
- **WHEN** a client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code
  `previous_response_not_found` and parameter `previous_response_id`
- **AND** the error uses the canonical continuity contract that allows Codex
  to retry without `previous_response_id`

#### Scenario: abandoned hard turn-state requests full-history recovery

- **GIVEN** operation admission finds an existing hard turn-state operation in
  `abandoned`
- **AND** the request has no `previous_response_id`
- **WHEN** the client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code `previous_response_not_found`
  without a `previous_response_id` parameter
- **AND** the error instructs Codex to discard the hard continuity anchor and
  resend full history
