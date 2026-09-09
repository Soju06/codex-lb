# account-routing Delta

## ADDED Requirements

### Requirement: Weighted strategies discount relatively slow upstream first-token latency

The balancer SHALL keep a replica-local, bounded window (3600 s, at most 64 samples per account) of upstream first-token latencies per account, sampled only from request-log rows with `status` `success`, `request_kind` `normal`, a recorded first-token latency, fewer than 20000 uncached input tokens (input tokens minus cached input tokens), reasoning effort absent, `minimal` or `low`, zero response-create-gate and bridge-queue wait, and a single upstream send with no account-capacity wait. The response-create-gate wait MUST include the time spent waiting for global response-create admission after the session gate is acquired, so a direct WebSocket turn that waited on the saturated global limit records a non-zero gate wait. Rows with `request_kind` `warmup`, `compaction` or `realtime_live`, error rows, rows that waited on the response-create gate, global response-create admission or the bridge queue, and WebSocket/bridge rows whose first-token latency spans a retried `response.create` send, a transparent direct-WebSocket replay (`replay_count` above zero) or an account-capacity wait (including a retry or replay that switched account) MUST NOT be sampled. When at least 3 accounts each hold at least 8 in-window samples, the `capacity_weighted` and `relative_availability` strategies MUST multiply each such account's draw weight by `1.0` when its estimate (the mean of its samples below the slowest decile) is within 15% above the fleet median of the per-account estimates, and by `max(0.5, fleet_estimate / account_estimate)` otherwise; with fewer accounts or fewer samples the multiplier MUST be neutral. The fleet reference MUST be computed over every account the replica tracks, not only the candidates of the current selection. The multiplier compounds with the error-rate multiplier, MUST NOT exclude any account, MUST NOT change `relative_availability` top-k membership or any deterministic probe pick, MUST NOT move an established sticky or continuity owner, and deterministic strategies (`round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain`, `single_account`) MUST be unaffected. The discount MUST lift as the window clears. The balancer MUST log a multiplier transition (crossing `1.0` or moving by more than `0.1`) without account identifiers. The window and multiplier are never persisted.

#### Scenario: A slow cohort receives less weighted traffic but is still drawn

- **GIVEN** three accounts with equal remaining credits under `capacity_weighted`
- **AND** two of them hold twenty eligible samples near 1.7 s and the third holds twenty near 6 s
- **WHEN** fresh selections are drawn
- **THEN** the slow account is drawn less often than either fast account
- **AND** it is still drawn (the 0.5 floor keeps sampling it)

#### Scenario: A uniformly slow fleet is neutral

- **GIVEN** twenty accounts whose eligible samples all sit near 2.6 s
- **WHEN** their draw weights are computed
- **THEN** every multiplier is `1.0`

#### Scenario: Thin evidence is neutral

- **GIVEN** only two accounts hold eight or more eligible samples, or an account holds seven
- **WHEN** draw weights are computed
- **THEN** every multiplier is `1.0`

#### Scenario: A large cached prefix keeps a small turn eligible

- **GIVEN** a successful `normal` row with 25000 input tokens of which 20000 are cached input tokens
- **WHEN** the row is written
- **THEN** it adds a sample to the account's window

#### Scenario: Ineligible rows are not sampled

- **GIVEN** request-log rows for an account with `status` `error`, `request_kind` `warmup`, `compaction` or `realtime_live`, no first-token latency, 20000 or more uncached input tokens, reasoning effort `medium` or `high`, a non-zero gate or bridge-queue wait, or a first-token latency that spans a retried send, a direct-WebSocket replay or an account-capacity wait
- **WHEN** the rows are written
- **THEN** none of them adds a sample to the account's window

#### Scenario: A transparently replayed direct WebSocket turn is not sampled

- **GIVEN** a direct WebSocket turn whose upstream dropped after `response.create` and that was transparently replayed once (`replay_count` is 1) with no queue wait
- **WHEN** the turn completes successfully and its request-log row is written
- **THEN** the row is marked as retried
- **AND** it adds no sample to the replacement account's window

#### Scenario: A direct WebSocket turn that waited for global admission is not sampled

- **GIVEN** a direct WebSocket turn that acquired the session gate immediately but waited 2.5 s for global response-create admission because the limit was saturated
- **WHEN** admission is granted
- **THEN** the turn's response-create-gate wait is recorded as 2500 ms
- **AND** its request-log row adds no sample to the account's window

#### Scenario: An established owner on a slow account is kept

- **GIVEN** a `prompt_cache` key already bound to an account whose multiplier is `0.5`
- **WHEN** the next turn selects with that key under `capacity_weighted`
- **THEN** the established owner is returned

#### Scenario: The discount lifts when the window clears

- **GIVEN** an account whose multiplier is `0.5`
- **WHEN** more than 3600 s pass without new eligible samples
- **THEN** its multiplier is `1.0` on the next state build

## MODIFIED Requirements

### Requirement: Transient balancer health signals are replica-local

Transient error counts, error-backoff windows, drain/probe health tiers, probe success streaks, in-flight/lease pressure, and recent first-token latency samples SHALL be maintained per replica
as advisory routing state and SHALL NOT require cross-replica agreement;
persisted account status, `reset_at`, and `blocked_at` transitions are the
only cross-replica health signals. Each replica SHALL converge on its own
observations.

#### Scenario: Peer may route to an account draining elsewhere

- **GIVEN** replica A has drained account X after locally observed transient errors
- **WHEN** replica B, which has recorded no errors for X, performs selection
- **THEN** replica B may select account X
- **AND** replica B backs off independently once its own error threshold for X is reached

#### Scenario: Peer weighs latency from its own samples

- **GIVEN** replica A holds enough first-token samples to discount account X
- **WHEN** replica B, which has recorded no samples for X, performs a weighted selection
- **THEN** replica B applies a neutral multiplier to account X
