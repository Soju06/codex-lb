## ADDED Requirements

### Requirement: Pool exhaustion is probed read-only with selection parity

The proxy SHALL provide a read-only pool-exhaustion probe that evaluates the structured usage-limit predicate over exactly the eligible pool ordinary account selection would use for the same request: the same model, the same requested `service_tier`, and the same API-key account scope narrowed by single-account routing (the selected account when it is inside the key's scope, nothing when no account is selected or the selected one lies outside that scope). Outside the drain strategies the probe MUST report exhaustion if and only if the deterministic opportunistic selector it rides on answers `usage_limit_reached` for that pool, and MUST carry that answer's `resets_at` and message unchanged so callers can rebuild the existing structured `429` verbatim. Under `sequential_drain`, `reset_drain` and `single_account` the probe MUST decline: it MUST report not exhausted without consulting the selector, MUST increment `codex_lb_pool_exhaustion_probe_declined_total{reason="drain_strategy"}` once and MUST log the reason with the strategy, because parity with foreground selection is established only outside the drain family — under it the two production paths draw from different budget subsets and return the subset's answer directly. Drain strategies therefore never trigger overflow in v1, and the routing stage MUST NOT bypass the decline. The probe takes the caller's dashboard-settings snapshot for this decision; a snapshot without a recognised strategy is not a drain strategy. The subscription-overflow admission decision is the probe's only routing-stage caller: it MUST call the probe with the dashboard-settings snapshot it read in its own first step (no second settings read), with the request's model and requested `service_tier`, after its O(1) declines and before any source resolution, body walk or admission claim, and MUST treat exactly the probe's exhaustion answer as the overflow trigger -- carrying that answer's `resets_at` and selection into the dispatch and rebuilding today's `429` from them on every later decline. Because the probe declines under the drain family, a `single_account`, `sequential_drain` or `reset_drain` deployment never overflows in this version; this reverses the design's recommendation that single-account exhaustion should trigger overflow and is an accepted owner decision, revisited only when the selector evaluates exhaustion over the full pool under those strategies. Every other selection answer — an admitted account, a closed opportunistic burn window, `no_accounts`, a plan or additional-quota gate, or a local capacity code — MUST be reported as not exhausted. The probe MUST evaluate without a lease kind, so local account caps (response-create and stream caps, the stream recovery reserve) MUST NOT mask exhaustion. The probe MUST be a pure observation: it MUST build the selector's states from a detached snapshot of the balancer runtime, so the live runtime is identical before and after the probe — no runtime entry is created, no account lease is acquired, no selection is recorded, no usage-derived health tier is refreshed and no runtime version advances — and it MUST NOT write sticky or persisted account state. The opportunistic admission check that admits opportunistic API-key traffic keeps its existing live housekeeping (stale-lease reclaim, runtime prune, health-tier refresh); the observation mode MUST be requested explicitly and MUST answer exactly as the live check would for the same inputs. The opportunistic admission check MUST forward the requested `service_tier` into selection-input loading so its plan and account eligibility filtering matches ordinary selection for the same request; when no tier is requested the check's inputs and answers MUST be unchanged.

#### Scenario: Usage-proven exhausted pool

- **GIVEN** every account eligible for the requested model is `QUOTA_EXCEEDED` with a usage window at 100 % and a known reset
- **WHEN** the pool is probed for that model
- **THEN** the probe reports exhaustion with the same `resets_at` ordinary selection returns for the same request
- **AND** the balancer runtime is identical before and after the probe, and the persisted account rows are unchanged
- **AND** probing again answers identically

#### Scenario: Service tier removes the last healthy account

- **GIVEN** an exhausted `pro` account and a healthy `plus` account both serving the requested model
- **AND** the model's `priority` service tier is available to `pro` plans only
- **WHEN** the pool is probed without a service tier
- **THEN** the probe reports not exhausted and ordinary selection admits the `plus` account
- **WHEN** the pool is probed with `service_tier` `priority`
- **THEN** the probe reports exhaustion with the `pro` account's reset
- **AND** ordinary selection at `priority` answers `usage_limit_reached` with the same reset

#### Scenario: No requested model spans every plan

- **GIVEN** accounts of two plans of which only one is exhausted
- **WHEN** the pool is probed without a model
- **THEN** the probe reports not exhausted
- **AND** once every account is exhausted the same probe reports exhaustion with the earliest exhausted-window reset

#### Scenario: Fresh rate limit without usage evidence

- **GIVEN** the only account is `RATE_LIMITED` with a block marker and a future reset but no usage window at 100 %
- **WHEN** the pool is probed
- **THEN** the probe reports not exhausted
- **AND** ordinary selection keeps its `no_accounts` failure semantics

#### Scenario: Local account caps do not mask exhaustion

- **GIVEN** an exhausted account whose in-flight streams sit at the configured stream cap
- **WHEN** opportunistic admission is checked with a `stream` lease kind
- **THEN** it answers `opportunistic_burn_window_closed`
- **WHEN** the same pool is probed
- **THEN** the probe reports exhaustion with the account's reset
- **AND** the runtime entry, including its in-flight stream count, is unchanged

#### Scenario: Observation does not refresh live health tiers

- **GIVEN** soft draining is enabled and an active account sits past the drain usage threshold
- **WHEN** the pool is probed
- **THEN** the probe reports not exhausted and the account's live runtime entry is not created or changed
- **WHEN** the same question is asked as a live opportunistic admission check
- **THEN** that check performs the ordinary health-tier refresh
- **AND** a subsequent probe leaves the refreshed runtime exactly as it found it

#### Scenario: Stale leases do not skew the observation

- **GIVEN** an account whose runtime still holds a stale stream lease that a live check would reclaim before building states
- **WHEN** the pool is probed
- **THEN** the probe evaluates the account without the stale lease's in-flight and token pressure and answers exactly as the live check
- **AND** the live runtime still holds the stale lease after the probe

#### Scenario: Selection parity over arbitrary pools outside the drain strategies

- **GIVEN** any pool of account states, reset preference and budget thresholds, and any routing strategy other than `sequential_drain`, `reset_drain` and `single_account`
- **WHEN** the deterministic opportunistic selector (secondary budget threshold applied, as the opportunistic admission check does) and foreground selection (secondary budget threshold not applied) evaluate their own copy of that pool at one instant
- **THEN** one answers `usage_limit_reached` exactly when the other does
- **AND** their `resets_at` and messages are equal whenever they do

#### Scenario: Selection parity under the drain strategies over a shared budget subset

- **GIVEN** any pool of account states, reset preference and budget thresholds under `sequential_drain`, `reset_drain` or `single_account`
- **WHEN** the deterministic opportunistic selector and foreground selection evaluate their own copy of that pool at one instant with the same secondary-budget-threshold setting
- **THEN** one answers `usage_limit_reached` exactly when the other does, with equal `resets_at` and messages

#### Scenario: The overflow trigger is the probe's answer

- **GIVEN** a stored subscription-overflow designation and a request that passed the decision's O(1) declines
- **WHEN** the decision probes the pool with the settings snapshot it read first, the request's model and its requested `service_tier`
- **THEN** it proceeds to source resolution only when the probe reports exhaustion, carrying the probe's `resets_at` and selection
- **AND** any other probe answer leaves the request on the unchanged subscription path
- **AND** the probe ran before any source query, portability view or admission claim and no second settings read happened

#### Scenario: Drain strategies never trigger overflow in v1

- **GIVEN** `sequential_drain`, `reset_drain` or `single_account` is the routing strategy and the pool is usage-exhausted
- **WHEN** the pool is probed
- **THEN** the probe reports not exhausted without asking the selector, increments `codex_lb_pool_exhaustion_probe_declined_total{reason="drain_strategy"}` once and logs `pool_exhaustion_probe_declined` naming the strategy
- **AND** the same pool probed under any other strategy reports exhaustion with the selector's `resets_at`
- **AND** the live runtime is untouched either way

#### Scenario: Drain strategies with a mixed exempt pool diverge between the production paths

- **GIVEN** `sequential_drain`, `reset_drain` or `single_account`, an available additional-quota-scoped `preserve` account (exempt from the exhaustion predicate) and a usage-exhausted account in the same pool
- **WHEN** the opportunistic admission check (secondary budget threshold applied) and foreground selection (not applied) evaluate that pool
- **THEN** the two production paths draw from different budget subsets and disagree on `usage_limit_reached`: foreground selection answers the structured `429`, the opportunistic selector does not
- **AND** this pre-existing divergence is the reason the probe declines under the drain family; the decline MAY be lifted only once the selector evaluates exhaustion over the full pool under those strategies
