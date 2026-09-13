# account-routing Delta

## ADDED Requirements

### Requirement: Weighted strategies discount relatively slow upstream first-token latency

The balancer SHALL keep a replica-local, bounded window (3600 s, at most 64 samples per account) of upstream first-token latencies per account, sampled only from request-log rows with `status` `success`, `request_kind` `normal`, a recorded first-token latency, an upstream send anchor, fewer than 20000 uncached input tokens (input tokens minus cached input tokens), reasoning effort absent, `minimal` or `low`, zero response-create-gate and bridge-queue wait, and a single upstream send with no account-capacity wait. The sampled latency MUST be measured from the upstream `response.create` send, not from request-state creation: the request-log funnel takes the row's start-to-send offset as `latency_upstream_send_ms` (not persisted) and the sample is `latency_first_token_ms - latency_upstream_send_ms`, so local pre-send work on the bridge path (session lookup and reconnect, prewarm, image inlining, payload slimming) and the direct WebSocket path (owner binding) is never attributed to the selected account, whether or not the later bridge-queue gate happened to be free. The WebSocket/bridge finalizer derives the offset from the turn's `response_create_sent_at` stamp; the HTTP stream path re-anchors its attempt clock after admission immediately before the upstream send and passes `0`. A row without a send anchor (`latency_upstream_send_ms` absent, or a send stamped after the first token) MUST NOT be sampled; the persisted `latency_first_token_ms` is unchanged. The response-create-gate wait MUST include the time spent waiting for global response-create admission after the session gate is acquired, so a direct WebSocket turn that waited on the saturated global limit records a non-zero gate wait. Rows with `request_kind` `warmup`, `compaction` or `realtime_live`, error rows, rows that waited on the response-create gate, global response-create admission or the bridge queue, and WebSocket/bridge rows whose first-token latency spans a retried `response.create` send, a transparent direct-WebSocket replay (`replay_count` above zero) or an account-capacity wait (including a retry or replay that switched account) MUST NOT be sampled. When at least 3 accounts each hold at least 8 in-window samples, the first-token multiplier of each such account MUST be `1.0` when its estimate (the mean of its samples below the slowest decile) is within 15% above the fleet median of the per-account estimates, and `max(0.5, fleet_estimate / account_estimate)` otherwise; with fewer accounts or fewer samples the multiplier MUST be neutral. The fleet reference MUST be computed over every account the replica tracks, not only the candidates of the current selection. The first-token multiplier is one input of the latency cohort multiplier (see "Latency cohort multipliers are combined by minimum"). The discount MUST lift as the window clears. The window is never persisted.

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

#### Scenario: Bridge pre-send work is not attributed to the account

- **GIVEN** an HTTP-to-WebSocket bridge turn whose request state was created at 0 ms, whose `response.create` was sent at 3000 ms after session lookup, reconnect and slimming, whose first token arrived at 4700 ms, with no gate or bridge-queue wait and a single send
- **WHEN** the turn completes successfully and its request-log row is written
- **THEN** the row's persisted first-token latency is 4700 ms
- **AND** the account's window gains a 1700 ms sample, not a 4700 ms one

#### Scenario: A row without an upstream send anchor is not sampled

- **GIVEN** an otherwise eligible WebSocket or bridge turn whose request state carries no `response.create` send stamp
- **WHEN** its request-log row is written
- **THEN** it adds no sample to the account's window

#### Scenario: A direct WebSocket turn that waited for global admission is not sampled

- **GIVEN** a direct WebSocket turn that acquired the session gate immediately but waited 2.5 s for global response-create admission because the limit was saturated
- **WHEN** admission is granted
- **THEN** the turn's response-create-gate wait is recorded as 2500 ms
- **AND** its request-log row adds no sample to the account's window

#### Scenario: The discount lifts when the window clears

- **GIVEN** an account whose multiplier is `0.5`
- **WHEN** more than 3600 s pass without new eligible samples
- **THEN** its multiplier is `1.0` on the next state build

### Requirement: Weighted strategies discount relatively slow per-model output throughput

The balancer SHALL keep a replica-local, bounded window (3600 s, at most 64 samples per account and model) of upstream output throughputs per (account, model), where a sample is the row's total `output_tokens` (reasoning tokens included) divided by its generation span in seconds: from the first token to the **upstream terminal event** (`response.completed`). The span MUST end at the instant the upstream terminal frame was parsed, captured before the frame is delivered downstream and before terminal bookkeeping, durable bridge writes, API-key settlement, deferred health writes and cleanup; neither downstream delivery time nor local settlement latency MUST be counted as generation time. Every transport stamps that instant at its own terminal-parse site and the request-log funnel receives it as `latency_upstream_terminal_ms` (not persisted; the persisted `latency_ms` keeps measuring to the row's write): the direct WebSocket reader and the HTTP-to-WebSocket bridge reader stamp `upstream_terminal_at` on the matched turn when they parse the terminal frame (the bridge's stamp precedes the durable alias, operation, recovery and circuit-settlement writes that run before its finalizer), and the HTTP stream stamps its stream settlement when it parses the terminal frame, before yielding it, so a slow downstream consumer or an upstream connection that stays open after `response.completed` does not stretch the span. A row whose terminal frame was never parsed has no stamp and is an error row, which is not sampled. A row MUST be sampled only when it has `status` `success`, `request_kind` `normal`, a non-empty model that is not the `unknown` placeholder, a recorded first-token latency, a positive generation span, at least 200 output tokens, zero response-create-gate and bridge-queue wait, and a single upstream send with no account-capacity wait; the input size and reasoning effort MUST NOT affect eligibility. Rows with `request_kind` `warmup`, `compaction` or `realtime_live`, error rows, queued rows, retried or replayed rows, rows without a model and rows with fewer than 200 output tokens MUST NOT be sampled. When a selection is performed for a model and at least 3 accounts each hold at least 8 in-window samples for that model, the throughput multiplier of each such account MUST be `1.0` when its estimate (the median of its samples for that model) is within 15% below the fleet median of the per-account estimates for that model, and `max(0.5, account_estimate / fleet_estimate)` otherwise; with fewer accounts, fewer samples, or no requested model the throughput multiplier MUST be neutral. Samples for one model MUST NOT contribute to the estimate, the fleet reference or the account count of another model. The fleet reference MUST be computed over every account the replica tracks for that model, not only the candidates of the current selection. The throughput multiplier is one input of the latency cohort multiplier (see "Latency cohort multipliers are combined by minimum"). The discount MUST lift as the window clears. The window is never persisted.

#### Scenario: A slow-throughput cohort on one model is discounted for that model

- **GIVEN** nineteen accounts with sol samples, nine near 40 tok/s and ten between 66 and 108 tok/s
- **WHEN** draw weights are computed for a sol selection
- **THEN** each of the nine accounts has a throughput multiplier of about `40 / 66`
- **AND** each of the ten accounts has a throughput multiplier of `1.0`

#### Scenario: An account slow on one model is not discounted on another

- **GIVEN** an account whose sol samples sit near 40 tok/s while two siblings sit near 70 tok/s
- **AND** the same three accounts hold astra samples near 50 tok/s
- **WHEN** draw weights are computed for an astra selection
- **THEN** the account's multiplier is `1.0`
- **AND** its multiplier for a sol selection is `40 / 70`

#### Scenario: A uniform fleet is neutral

- **GIVEN** twenty accounts whose sol samples all sit within 44 and 56 tok/s
- **WHEN** draw weights are computed for a sol selection
- **THEN** every multiplier is `1.0`

#### Scenario: Thin per-model evidence is neutral

- **GIVEN** two accounts with eight or more sol samples, a third with eight astra samples only, and a fourth with seven sol samples
- **WHEN** draw weights are computed for a sol selection
- **THEN** every multiplier is `1.0`

#### Scenario: A build without a requested model does not consult throughput

- **GIVEN** an account whose sol throughput multiplier would be `0.5`
- **WHEN** states are built without a model (quota planner, model-less selection)
- **THEN** its throughput multiplier is `1.0`

#### Scenario: A qualifying row records a throughput sample

- **GIVEN** a successful `normal` sol row with 60000 input tokens, reasoning effort `high`, 400 output tokens, 2000 ms first-token latency and its upstream terminal event at 12000 ms, no queue wait and a single send
- **WHEN** the row is written
- **THEN** it adds a 40 tok/s sample to the account's sol window
- **AND** it adds no first-token sample (the row is outside the first-token slice)

#### Scenario: Delayed API-key settlement does not dilute a WebSocket throughput sample

- **GIVEN** a successful keyed WebSocket sol turn whose first token arrived at 2000 ms, whose `response.completed` was parsed at 12000 ms with 400 output tokens, and whose API-key settlement then takes 10 s
- **WHEN** the finalizer settles the turn and writes its row
- **THEN** the row's `latency_ms` is 22000 ms
- **AND** the account's sol window gains a 40 tok/s sample, not a 20 tok/s one

#### Scenario: A slow downstream consumer does not dilute an HTTP throughput sample

- **GIVEN** a successful HTTP stream sol turn whose first token arrived at 2000 ms and whose `response.completed` frame was parsed at 12000 ms with 400 output tokens
- **AND** the downstream client takes 5 s to drain the terminal frame and the upstream connection lingers 5 s more before the generator closes
- **WHEN** the row is written
- **THEN** the row's `latency_ms` is 22000 ms
- **AND** the account's sol window gains a 40 tok/s sample, not a 20 tok/s one

#### Scenario: Bridge bookkeeping after the terminal does not dilute a throughput sample

- **GIVEN** a successful HTTP-to-WebSocket bridge sol turn whose first token arrived at 2000 ms and whose `response.completed` frame was parsed by the bridge reader at 12000 ms with 400 output tokens
- **AND** durable alias, operation and circuit-settlement writes take 10 s before the finalizer runs and its API-key settlement takes 10 s more
- **WHEN** the finalizer writes the row
- **THEN** the row's `latency_ms` is 32000 ms
- **AND** the account's sol window gains a 40 tok/s sample
- **AND** the turn's terminal stamp was set when the frame was parsed, before the finalizer was entered

#### Scenario: Queued, replayed, short and model-less rows record no throughput sample

- **GIVEN** otherwise qualifying rows with a non-zero bridge-queue or gate wait, a retried or replayed send, 199 output tokens, or no model
- **WHEN** the rows are written
- **THEN** none of them adds a sample to any per-model window

### Requirement: Latency cohort multipliers are combined by minimum

For each candidate of a state build, the balancer SHALL compute the first-token multiplier and the throughput multiplier for the requested model and MUST multiply the candidate's draw weight by the smaller of the two; the two multipliers MUST NOT be multiplied together. The combined multiplier compounds with the error-rate multiplier and is consulted only by the `capacity_weighted` and `relative_availability` draws: it MUST NOT exclude any account, MUST NOT change `relative_availability` top-k membership or any deterministic probe pick, MUST NOT move an established sticky or continuity owner, and deterministic strategies (`round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain`, `single_account`) MUST be unaffected. The state build MUST receive the requested model from the selection that triggered it -- on the unbound path, on the sticky path when the key has no owner, and on both the live and the observe-only opportunistic admission builds -- so the throughput multiplier is model-aware. The balancer MUST log a transition of either multiplier (crossing `1.0` or moving by more than `0.1`), naming the combined multiplier, the signal that set it (`ttft`, `tps`, `both` or `none`), the model and which signal transitioned, without account identifiers. The first-token transition MUST be gated per account, so one first-token transition is logged once regardless of how many models (or none) are requested; the throughput transition MUST be gated per account and requested model. The balancer MUST retain a per-model throughput weight only while it is below `1.0`, so the per-account map is bounded by the models the account is discounted on and a neutral build for an unknown or evidence-less model retains nothing. Neither multiplier is persisted.

#### Scenario: An account slow on both signals is discounted once

- **GIVEN** an account whose first-token multiplier is `0.5` and whose sol throughput multiplier is `0.6`
- **WHEN** draw weights are computed for a sol selection
- **THEN** its combined multiplier is `0.5`, not `0.3`

#### Scenario: The transition log names the signal and the model

- **GIVEN** three accounts with sol throughput samples of which one sits near 40 tok/s and two near 70 tok/s, and no first-token samples
- **WHEN** draw weights are computed for a sol selection
- **THEN** one transition is logged with `signal=tps` and `model=gpt-5.6-sol`, and the line contains no account identifier
- **AND** alternating astra and sol builds do not log the sol transition again

#### Scenario: A first-token transition is logged once across models

- **GIVEN** three accounts with first-token samples of which one sits near 6 s and two near 1.7 s, and no throughput samples
- **WHEN** draw weights are computed for a sol selection, an astra selection, a model-less build and a selection for a model without evidence
- **THEN** exactly one transition is logged, with `signal=ttft`
- **AND** the account retains no per-model throughput weight

#### Scenario: A neutral build for an unknown model retains nothing

- **GIVEN** an account with sol throughput evidence and siblings that make it slow on sol
- **WHEN** draw weights are computed for a model string with no evidence, the `unknown` placeholder, an empty model or no model
- **THEN** every multiplier is `1.0`
- **AND** the account retains no per-model throughput weight for any of those keys

#### Scenario: A sticky fresh draw is weighted for the requested model

- **GIVEN** a `codex_session` key with no owner and an account that is slow on sol and uniform on astra
- **WHEN** the key selects with `model` sol
- **THEN** the build applied the account's sol throughput multiplier
- **AND** a fresh key selecting with `model` astra applies `1.0` for the same account

#### Scenario: Opportunistic admission builds states for the requested model

- **GIVEN** an account that is slow on sol
- **WHEN** an observe-only admission check runs for sol
- **THEN** the detached build applied the account's sol throughput multiplier and the live runtime retains no weight
- **AND** a live admission check for sol records the weight and selects the same account as the observation

#### Scenario: An established owner on a slow account is kept

- **GIVEN** a `prompt_cache` key already bound to an account whose combined multiplier is `0.5`
- **WHEN** the next turn selects with that key under `capacity_weighted`
- **THEN** the established owner is returned

## MODIFIED Requirements

### Requirement: Transient balancer health signals are replica-local

Transient error counts, error-backoff windows, drain/probe health tiers, probe success streaks, in-flight/lease pressure, recent first-token latency samples and recent per-model output-throughput samples SHALL be maintained per replica
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

- **GIVEN** replica A holds enough first-token or throughput samples to discount account X
- **WHEN** replica B, which has recorded no samples for X, performs a weighted selection
- **THEN** replica B applies a neutral multiplier to account X
