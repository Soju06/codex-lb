# Design: add-account-usage-limits

## Context

`Account` stores operator-controlled routing fields, while `AccountState` is the canonical selector input used by ordinary, sticky, single-account, opportunistic, bridge, file, and gated-model routes. Standard quota rows are loaded alongside additional-quota rows, but gated-model selection can replace the priority rows used for ranking. A standard account limit therefore cannot be implemented as another soft budget filter or by reading only `AccountState.used_percent`.

## Goals

- Let an operator cap one account at a maximum observed used percentage such as 10%.
- Make the cap reversible without losing the configured percentage.
- Ensure every selector and fallback respects the cap.
- Keep standard account-limit state separate from persisted upstream account status.
- Degrade toward preserving quota when usage telemetry is unavailable.

## Decisions

### D1: Persist configuration and activation separately

Accounts gain nullable default `usage_limit_percent`, nullable `usage_limit_5h_percent` and `usage_limit_weekly_percent` overrides, and a non-null `usage_limit_enabled` field. A disabled row retains its percentages for re-enablement; removing the limit clears all three and disables it. An enabled policy requires at least one percentage. Each percentage is greater than 0 and at most 100.

### D2: Effective caps apply to their current standard quota windows

The evaluator normalizes weekly-only and monthly-only account shapes using the same window rules as routing and account presentation. The 5-hour and weekly overrides replace the default only for matching normalized durations; monthly and other durations use only the default. A missing override inherits the default, while an absent default leaves unmatched windows unrestricted. An enabled account is blocked when any current standard window reports `used_percent` at or above its effective cap.

When historical monthly telemetry and a normalized weekly-only shape coexist, they are alternative observations of the account's long-window quota rather than independent windows. The evaluator chooses between them with the shared sibling-fetch rules: timestamps more than the sibling margin apart establish fetch order; rows within the margin use quota metadata and reset deadlines; an exact tie keeps the stable weekly-primary default. A genuinely newer weekly observation can therefore replace an elapsed or stale monthly row after an upstream shape/reset transition, while a newer or same-fetch authoritative monthly observation remains canonical.

Rows whose reset deadline elapsed are not exhaustion evidence. Observations for applicable capped windows must be fresh according to the existing usage-refresh freshness window; a missing, stale, or unknown observation for one of those windows makes the limit state `data_unavailable` and selection fails closed. Unrestricted windows do not require telemetry. This favors preserving quota over availability while a hard operator policy is active.

The reusable evaluator returns one of `disabled`, `available`, `reached`, or `data_unavailable`. Account summaries and proxy state use the same evaluator so the dashboard cannot claim a different cap state from the selector.

### D3: Standard usage remains available during additional-quota routing

Selection inputs retain cloned standard primary, secondary, and monthly rows separately from request-priority rows. Gated models may still rank and validate against their additional quota, but the hard account limit is always evaluated from standard rows and is never bypassed by `ignore_standard_quota`.

### D4: The canonical selector owns the hard gate

`AccountState` carries the evaluated limit state. `select_account` applies the policy after status, upstream-quota, and cooldown checks, but before error-backoff classification, health, stickiness, policy, or strategy handling. This makes the local policy authoritative only for accounts that would otherwise be routing candidates: an account that is also upstream-exhausted retains the established `usage_limit_reached` 429 and reset metadata, while a locally blocked account never enters the error-backoff fallback set. If all otherwise eligible candidates are blocked by limits, selection returns HTTP 429 with stable error code `account_usage_limit_reached` and local `rate_limit_error` type but no upstream reset deadline; opportunistic prechecks preserve that typed error instead of rewriting it. The account's persisted `active`/rate-limit status is not changed.

Fair-share admission derives capacity and lease/key counters from the same usage-policy-eligible candidate set used for routing. Locally blocked accounts contribute neither capacity nor in-flight counters; an entirely locally blocked pool bypasses fair-share admission and reaches the canonical policy error. A hard-sticky owner blocked by the policy also bypasses peer-pool fair-share denial: congestion relief cannot make that owner eligible, so the owner selector returns `account_usage_limit_reached` immediately while preserving the mapping.

Reused HTTP bridges and proxy WebSockets are continuity owners, not fresh selection opportunities. Their newly admitted turns use an account-scoped authorization query that reads the pinned account policy and latest standard windows in one database statement. HTTP bridges check before queue admission and again immediately before visible or prewarm dispatch; proxy WebSockets check before response-create lease acquisition and immediately before each `response.create` dispatch. These reads do not clone or scan the fleet and never ask the selector for an alternate owner. The final HTTP bridge read remains under the session lifecycle lock so a reconnect cannot change its account between authorization and send. A five-second timeout maps a stalled read to `authorization_failed`, bounding how long it can delay other session lifecycle work. WebSockets recheck the upstream reconnect latch after final authorization and transfer an unsent frame to the reconnect path. An HTTP policy denial marks the session to retire after drain so already-admitted turns keep their ownership and settlement paths; a WebSocket denial rejects only the new frame. A missing or administratively unavailable owner uses the established continuity-lost response instead of a local usage-limit error.

Every synthetic warmup surface uses the same hard policy. The public `/v1/warmup` path evaluates standard primary, secondary, and monthly rows while choosing targets in every mode; `force` bypasses only its legacy zero-percent primary-window heuristic, never the operator limit. It then reloads the account and standard observations after credential refresh and immediately before each compact dispatch so a policy or status change after planning fails the target closed without upstream traffic. Quota warmup planning consumes the usage-limit state already evaluated on `AccountState`. Because a planned action can outlive that snapshot, execution freshly reads the account plus its standard primary, secondary, and monthly rows after the decision claim and optional API-key reservation, then requires the account to remain active and runs the same evaluator immediately before the probe send. A denial releases the reservation and changes the claimed decision from `executing` to `skipped`. Disabled and available policies remain neutral.

### D5: Dashboard writes invalidate selection state across replicas

`PUT /api/accounts/{account_id}/usage-limit` accepts the enabled flag, optional default percentage, and optional 5-hour and weekly override percentages. An omitted percentage retains its stored value; explicit null clears it. The API returns all persisted policy fields, invalidates the local selection-input cache, and emits the existing account-selection invalidation signal for peers. Account summaries expose the fields and evaluated state. The Accounts page edits shared and applicable window reserves, with `10% reserved` corresponding to a `90% maximum used` API threshold. The dashboard initializes editable values from persisted numbers without decimal-place quantization, so every API-valid percentage remains valid and unchanged until the operator edits it.

### D7: Owner authorization is a total decision, not an optional policy state

`app/modules/usage/authorization.py` owns `OwnerAuthorization`, whose kind is
`allowed`, `usage_policy_blocked`, `owner_unavailable`, or
`authorization_failed`. Only `allowed` grants permission. Policy denial retains
the evaluator's `reached` versus `data_unavailable` reason. The result also
carries the authoritative account status and standard-usage snapshot so warmups
can apply their separate short-window heuristic without another read.

`LoadBalancer.authorize_account_fresh`, HTTP bridge admission, WebSocket
`response.create`, public warmup, limit warmup, and quota warmup consume this
contract. Warmups request an active owner; ordinary routing still owns transient
rate-limit/quota recovery and additional-quota semantics. Missing, paused,
and deactivated owners are unavailable regardless of policy. A reauthentication
warning remains routable while its token is not known to have expired; transports
reject a known-expired token. Repository/context failures are explicit local authorization
failures. Cancellation remains an exception, not a policy state.

A final selection attempt maps unavailable owners to
`preferred_account_unavailable`, not `account_usage_limit_reached`. Cache-generation
retries remain bounded to four attempts per selection phase. If invalidations exhaust retries, the selected owner
is freshly authorized; denial releases its lease and provisional recovery probe
before returning, and no new sticky owner is published. The mapping is
exhaustive so adding another decision kind cannot implicitly grant permission.

Provisional selection release is an owned cleanup operation. Repeated
cancellation while it waits for the runtime lock cannot interrupt lease/token
and recovery-probe release. Cleanup finishes before propagating cancellation;
cleanup failures are logged without replacing the original cancellation.

### D8: Routing capacity is a projection, not the complete pool

`RoutingPoolEvaluation` performs one eligibility pass on cloned states and
projects membership back onto the original states. It retains the full pool,
normal candidates, traffic-class capacity candidates, and routable candidates.
Fair-share admission and concurrency caps reuse that view. Cap filtering removes
only cap-denied routable candidates, not the administrative/quota/cooldown/policy
evidence needed by canonical fallback and terminal error selection.

Pre-feature public account loading already excluded paused/deactivated peers, and
that remains unchanged. A supplied canonical pool containing a paused peer must
preserve its evidence; actual public rate-limited/quota-exceeded peers remain in
the loaded pool and preserve the established controlled backoff fallback. Tests
cover both cases without introducing a new public routing behavior.

### D9: History and current authorization have different measurement contracts

The existing account-scoped snapshot query is retained as the single atomic
read of policy/status and current standard windows. Its typed window projection
represents unavailable measurement as `used_percent=None`; latest-state reads
must not skip unknown rows and revive an older measurement. Disabled-policy
snapshots retain their windows because quota warmup still needs those windows
for its independent planning rules.

Analytic readers use the centralized SQL measurement predicate before numeric
calculations, including before `lag()` in positive demand deltas. The same
predicate now covers individual history, bulk history, aggregates and trends.
A real zero with reset/window metadata remains a measured sample; the legacy
zero/no-metadata placeholder is not a sample. The SQLite direct-read predicate
has equivalent semantics and is exercised along with PostgreSQL.

### D10: Local authorization failure is not upstream HTTP telemetry

`account_usage_limit_authorization_failed` is registered as a local proxy error,
so full request-log metadata leaves upstream HTTP status absent for a local
503. The local failure retains its provenance without changing account health or
circuit-breaker penalties.

### D11: Policy edits are serialized through cache reconciliation

The existing TanStack Query mutation now cancels account-list and dashboard
queries before publishing the server's acknowledged policy. Cancellation also
fences a promise whose underlying fetch ignores AbortSignal. Inactive queries
matter: invalidation alone does not refetch them and did not prevent their old
response from overwriting a saved policy.

Policy edits share the `account-usage-limit` mutation scope across controls in a
QueryClient. A later enable/change/disable does not begin until the previous
mutation's reconciliation and required account-list refresh finish. The account
refresh remains awaited; dashboard refresh retains its existing non-blocking UX.
This serializes this infrequent human-facing feature rather than adding a policy
revision column or changing all account query schemas. Independent clients can
still make newer legitimate server-side changes; this is not a distributed edit
lock. Locked-dependency React tests cover inactive reads and overlapping edits.

### D12: Provisional resource cleanup must have a usable transaction

Quota warmup commits its claim and optional API-key reservation before the final
read. Cleanup first rolls back the read transaction, then releases reserved
capacity and marks the claimed decision skipped. This is necessary for actual
PostgreSQL statement errors and driver cancellation; mocked exceptions alone
missed the invalid-transaction failure. The shared result-preserving
cancellation helper owns cleanup until it finishes, then propagates cancellation.

SQLite's long-write diagnostics must not access `Connection.info` on an
invalidated/closed connection: that property can attempt a reconnect before the
required rollback and prevent cleanup itself. Diagnostic callbacks now leave
those connections alone. Tests interrupt real SQLite and PostgreSQL SQL and
verify the reservation, budget and warmup claim from a separate session.

### D13: Consistency is defined at observation boundaries

The policy API commits the persisted policy fields, invalidates its local selection cache,
and awaits the existing best-effort peer invalidation bump before returning.
These are the visibility rules, not a promise of an exact spend ceiling:

| Surface | Visibility after an acknowledged policy change |
| --- | --- |
| Subsequent same-replica fresh selection | The invalidated input cache is reloaded. In-progress attempts detect generation changes and retry, with explicit fresh authorization on retry exhaustion. |
| Existing HTTP bridge or WebSocket owner | A new final authorization read observes committed policy/status from the shared database, independently of selection-cache invalidation. |
| Peer-replica fresh selection | A successful invalidation is observed on the existing poll cycle (default 0.5 seconds); the input-cache TTL (default 5 seconds from fill) is the fallback if the signal fails or no poller runs. These intervals exclude scheduling/database delay. |
| Public/limit/quota warmup execution | Each final gate freshly authorizes the chosen owner; a previously planned action is not permission to dispatch. |
| Live telemetry | Only committed observations can be authorized. Global cache invalidation retains its five-second trailing schedule; owner reads need not wait for that invalidation. |

A policy or usage change after a particular authorization read is not made
retroactive, and requests already dispatched keep their settlement ownership.
Queue admission, optional prewarm and actual visible dispatch remain distinct
checks: another task or a wait can intervene between them. A reused bridge with
a held stream lease has two owner reads for a normal visible turn (queue and
dispatch); idle lease reacquisition adds one, and an actual prewarm dispatch adds
its own gate. WebSocket response-create can read once before lease admission and
again at the dispatch boundary, because lease admission and authorization are
not atomic. No unbounded retry or owner reroute is introduced.

The final authorization boundary remains a single account-scoped database read;
cached ordinary selection adds no database reads for this policy check.

## Migration

The scalar Alembic revision follows upstream `20260918_000000_merge_scim_and_overflow_heads` and adds `usage_limit_enabled` and nullable `usage_limit_percent` with range and enabled/value checks. Existing accounts remain disabled with no percentage. The subsequent `20260910_010000_add_usage_limit_overrides` revision adds nullable 5-hour and weekly percentages, checks each range, and permits an enabled policy when any of the three percentages is present. Downgrading the override revision first disables policies that have no scalar percentage; downgrading the scalar revision then removes its checks and columns. Batch operations support SQLite and PostgreSQL round trips.
