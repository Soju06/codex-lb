## ADDED Requirements

### Requirement: Overload rejections deprioritize the account for fresh selection

When upstream rejects a fresh admission for an account as overloaded
(`server_is_overloaded` or `overloaded_error`), the proxy MUST record the
rejection in a replica-local per-account window that is independent of the
transient error count and MUST NOT be reset by later successes on that
account. When at least three rejections land inside a 120-second window, the
proxy MUST deprioritize the account for fresh (unbound) selection for a
bounded interval that grows exponentially with consecutive trips (60 seconds
base, capped at 600 seconds, decaying to the base after 30 minutes without a
trip); a trip while already deprioritized MUST NOT shorten the deadline.
Deprioritization MUST be soft: the account is removed from the fresh-selection
candidate pool only while at least one other candidate remains, and it MUST
NOT be applied to sticky, continuity-owner, or hard-affinity selection. The
proxy MUST log when the backoff engages. The failure classification, the
failover decision, the existing transient error penalty, and the status and
body returned to the client MUST remain unchanged.

#### Scenario: Warm sessions keep masking the generic error counters

- **GIVEN** account A is rejected as overloaded on fresh admissions while its
  bridge-reuse and sticky sessions keep succeeding
- **WHEN** three rejections arrive within 120 seconds
- **THEN** account A enters overload backoff even though its transient error
  count is zero
- **AND** fresh selection skips account A while another candidate is available

#### Scenario: Backoff never empties the pool

- **GIVEN** every selectable account is in overload backoff
- **WHEN** a fresh request selects an account
- **THEN** selection proceeds over the full candidate pool as if no account
  were backed off

#### Scenario: Pinned sessions are not denied by overload backoff

- **GIVEN** account A is in overload backoff
- **WHEN** a request hard-pinned to account A (continuity owner, sticky
  session, or file affinity) selects an account
- **THEN** the pin is honored exactly as before

#### Scenario: Consecutive trips back off longer, bounded

- **GIVEN** account A trips the window repeatedly with each burst starting when
  the previous backoff expires
- **WHEN** the backoff deadline is computed for each trip
- **THEN** the interval doubles from 60 seconds and never exceeds 600 seconds
- **AND** after 30 minutes without a trip the next trip returns to 60 seconds

#### Scenario: Non-overload transient errors do not feed the window

- **GIVEN** account A returns a transient `server_error`
- **WHEN** the proxy records account health
- **THEN** the generic transient error is recorded as before
- **AND** the overload window for account A is unchanged
