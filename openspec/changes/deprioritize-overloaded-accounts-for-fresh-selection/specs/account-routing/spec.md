## ADDED Requirements

### Requirement: Overload rejections deprioritize the account for fresh selection

When upstream rejects a fresh admission for an account as overloaded
(`server_is_overloaded` or `overloaded_error`), the proxy MUST record the
rejection, at the point where account health is written for it, in a
replica-local per-account window that is independent of the transient error
count and MUST NOT be reset by later successes on that account. When at least three rejections land inside a 120-second window, the
proxy MUST deprioritize the account for fresh (unbound) selection for a
bounded interval that grows exponentially with consecutive trips (60 seconds
base, capped at 600 seconds, decaying to the base after 30 minutes without a
trip); the level MUST saturate once the cap is reached so sustained overload
cannot grow it without bound, and a trip while already deprioritized MUST NOT
shorten the deadline.
Deprioritization MUST be soft: selection first runs over the candidates not in
overload backoff and, when the configured strategy and budget gates select
none of them, runs again over the full candidate pool exactly as before. It
MUST apply wherever a NEW account is chosen for a request — unbound selection
and the sticky path's fresh binding, reallocation, or fallback pick — and MUST
NOT apply to an established sticky owner, a continuity owner, or a
hard-affinity owner. The
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

#### Scenario: Backoff yields to an ineligible remainder

- **GIVEN** account A is in overload backoff and the configured strategy
  selects none of the other accounts (rate-limited, cooling down, in generic
  error backoff, or excluded by the strategy's budget gates)
- **WHEN** a fresh request selects an account
- **THEN** account A is selected rather than failing the request or reporting
  an account-cap error

#### Scenario: A previously unseen sticky key binds away from the backed-off account

- **GIVEN** account A is in overload backoff and account B is selectable
- **WHEN** a request carrying a session or prompt-cache key with no established
  owner selects an account
- **THEN** the new binding is made to account B
- **AND** a request whose key already maps to account A keeps using account A

#### Scenario: HTTP-status overload rejections keep their code

- **GIVEN** upstream answers a fresh admission with an HTTP 5xx whose body
  carries `server_is_overloaded`
- **WHEN** the same-account transient retries are exhausted and health is
  written after settlement
- **THEN** the health write carries `server_is_overloaded` rather than a
  collapsed `server_error`, so the rejection counts toward the account's
  overload window

#### Scenario: Recovery-probe reservation follows the selected pool

- **GIVEN** a fresh request whose selection ran over the overload-free
  candidates
- **WHEN** the selected account is a due recovery probe that needs a
  reservation
- **THEN** the reservation is taken from that same overload-free pool, so an
  older due probe skipped by the overload pass cannot invalidate the selection

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
