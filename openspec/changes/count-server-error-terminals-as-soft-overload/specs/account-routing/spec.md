## MODIFIED Requirements

### Requirement: Overload rejections deprioritize the account for fresh selection

When upstream rejects a fresh admission for an account as overloaded
(`server_is_overloaded` or `overloaded_error`), the proxy MUST record the
rejection, at the point where account health is written for it, in a
replica-local per-account window that is independent of the transient error
count and MUST NOT be reset by later successes on that account.

Upstream also refuses an admitted turn with a bare `server_error` stream
terminal, which is the same observable condition without the explicit code. The
proxy MUST record such a terminal in a second replica-local window that shares
the same 120-second horizon, and MUST count each of those observations at a
fractional weight strictly between zero and one toward the same trip threshold,
so a single `server_error` fault can never trip the window on its own while a
sustained refusal still does. The terminal shape is what makes the observation
an admission rejection, so the proxy MUST record it only when the failure
carries no upstream HTTP status. A coded HTTP failure whose body reports the
same string is an ordinary transient error and MUST NOT enter this window, and
an upstream HTTP 429 carrying `server_error` is a per-account burst rejection
that MUST continue to take the short replica-local burst cooldown instead.

Some failures reach the account-health write through a terminal renderer that
has already discarded the response object, and others are delivered as a
terminal frame that itself carries the upstream status. A caller on either
shape MUST be able to report the upstream HTTP status to this window alone,
without thereby altering the failure classification, the account-neutral and
model-scoped rejection exemptions, the reasoning-replay counter, or the HTTP
429 burst-cooldown path, all of which keep their existing inputs unchanged.
Where such a status is reported, the failure MUST stay out of the soft window.

Both windows MUST be pruned by the same horizon and cleared together when the
window trips.

When the combined weight of both windows reaches at least three inside a 120-second window, the
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

#### Scenario: A lone bare server_error terminal does not deprioritize

- **GIVEN** account A is healthy
- **WHEN** one `server_error` stream terminal arrives with no HTTP status
- **THEN** account A is not deprioritized for fresh selection
- **AND** no burst cooldown is engaged

#### Scenario: Sustained bare server_error terminals deprioritize the account

- **GIVEN** account A is healthy
- **WHEN** six `server_error` stream terminals arrive within 120 seconds
- **THEN** account A enters overload backoff

#### Scenario: Explicit and bare rejections combine toward one threshold

- **GIVEN** account A has recorded two `server_error` terminals inside the window
- **WHEN** two `server_is_overloaded` rejections arrive inside the same window
- **THEN** account A enters overload backoff

#### Scenario: A coded HTTP server_error failure never enters the soft window

- **GIVEN** account A is healthy
- **WHEN** six upstream HTTP 500 failures whose body code is `server_error`
  arrive within 120 seconds
- **THEN** account A is not deprioritized for fresh selection

#### Scenario: HTTP 429 carrying server_error stays on the burst cooldown path

- **GIVEN** upstream returns HTTP 429 whose body code is `server_error`
- **WHEN** account health is written for it
- **THEN** the short replica-local burst cooldown is engaged
- **AND** the soft overload window records nothing

#### Scenario: An HTTP 5xx server_error surfaced by the terminal renderer stays out of the soft window

- **GIVEN** upstream answered with an HTTP 500 whose body code is
  `server_error`, and the terminal renderer writes account health from the
  settled failure rather than from the response object
- **WHEN** six such failures are recorded within 120 seconds
- **THEN** the soft overload window records nothing and account A is not
  deprioritized for fresh selection
- **AND** the account-neutral and model-scoped rejection exemptions, the
  reasoning-replay counter, and the burst cooldown behave exactly as they did
  before the status was reported

#### Scenario: A bridge terminal error frame carrying an HTTP status stays out of the soft window

- **GIVEN** an HTTP-bridge terminal `error` frame whose payload carries
  `status: 500` and whose normalized code is `server_error`
- **WHEN** account health is written for it
- **THEN** the soft overload window records nothing
- **AND** an equivalent frame carrying no status is still recorded as a soft
  observation
