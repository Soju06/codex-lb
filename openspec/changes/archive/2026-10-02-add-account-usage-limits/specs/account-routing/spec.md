## ADDED Requirements

### Requirement: Accounts have a reversible usage-limit policy

Each account SHALL support an optional default maximum used percentage, optional 5-hour and weekly override percentages, and one enabled flag. Each percentage MUST be greater than 0 and at most 100, and the API MUST reject other values. The policy SHALL default to disabled for new and existing accounts. In an update, an omitted percentage MUST keep its stored value, null MUST clear it, and a number MUST replace it. An enabled update MUST leave at least one stored percentage; otherwise it MUST be rejected without changing the stored policy. Disabling SHALL keep the saved percentages, and removal SHALL clear all of them and disable the policy. The API MUST commit the policy and invalidate local selection inputs before it acknowledges the update.

#### Scenario: Operator disables and re-enables a saved policy

- **GIVEN** an account has an enabled maximum usage of 10 percent
- **WHEN** the operator disables the policy and later re-enables it by sending only the enabled flag
- **THEN** routing ignores the policy while it is disabled
- **AND** the saved 10 percent applies again once it is re-enabled

#### Scenario: Stale dashboard toggles without reverting newer values

- **GIVEN** a dashboard loaded a maximum of 10 percent
- **AND** another client changed the stored maximum to 20 percent
- **WHEN** the stale dashboard toggles the policy by sending only the enabled flag
- **THEN** the stored maximum remains 20 percent

#### Scenario: Re-enabling a removed policy fails

- **GIVEN** a dashboard cached a disabled policy that another client has since removed
- **WHEN** the stale dashboard enables the policy
- **THEN** the request is rejected
- **AND** no cached percentage is restored

### Requirement: Usage-limit thresholds apply per normalized window

After weekly-only and monthly-only account shapes are normalized, an override SHALL replace the default only for the window whose duration matches it: 300 minutes for the 5-hour override and 10080 minutes for the weekly override. Monthly and other windows SHALL use only the default. A window without an override SHALL inherit the default, and a window with neither SHALL be unrestricted. A window SHALL reach its limit when its current used percentage is greater than or equal to its effective threshold. When historical monthly and normalized weekly-only shapes coexist, observations from fetches further apart than the shared sibling-fetch margin MUST be ordered by `recorded_at`; closer observations MUST use quota metadata and reset-deadline precedence, and the weekly-primary shape MUST win an exact tie.

#### Scenario: Unequal window thresholds

- **GIVEN** a default of 80, a 5-hour override of 70, and a weekly override of 90
- **WHEN** current 5-hour usage is 65 percent and weekly usage is 75 percent
- **THEN** the account remains available

#### Scenario: Equality reaches the limit

- **GIVEN** an enabled maximum usage of 10 percent
- **WHEN** the current weekly observation is exactly 10 percent used
- **THEN** the policy state is `reached`

#### Scenario: Weekly override does not govern monthly usage

- **WHEN** only a weekly override is enabled on a monthly-only account
- **THEN** monthly usage remains unrestricted

#### Scenario: Fresh weekly shape supersedes elapsed monthly telemetry

- **GIVEN** a monthly-capable account has an old elapsed monthly observation
- **AND** a later weekly-only primary observation reports usage below the maximum
- **WHEN** the policy is evaluated
- **THEN** it uses the weekly observation and the policy state is `available`

### Requirement: Enabled usage limits fail closed without current data

An enabled policy MUST evaluate to `data_unavailable` when a window with an effective threshold has no current observation, only a stale observation, or no used percentage. Elapsed window rows MUST NOT count as exhaustion evidence. Windows without an effective threshold MUST NOT block the policy, and an unknown-duration placeholder alone MUST NOT establish an unrestricted account shape. An enabled override MUST require a current matching observation unless the plan has known zero capacity for that window or the account has a normalized monthly-only shape.

For an account whose policy is enabled when the poll returns, a successful usage poll without standard windows MUST supersede older standard observations in every applicable slot, including a weekly window stored in the primary slot, a reported window without a used percentage MUST supersede the older observation in its slot, and selection inputs MUST be invalidated. An account without an enabled policy MUST keep its stored observations and its disabled-policy routing behavior. Once standard telemetry commits for an enabled policy, cancellation of a later read in the same refresh MUST NOT skip selection invalidation, and a cancelled policy read MUST invalidate conservatively before cancellation propagates.

#### Scenario: Missing observations block the account

- **GIVEN** an account has an enabled policy and no current standard observation
- **WHEN** the selector evaluates the account
- **THEN** the account is not selected and its state is `data_unavailable`

#### Scenario: Override has no current matching observation

- **GIVEN** an enabled 5-hour override on a plan with positive or unknown 5-hour capacity
- **WHEN** the 5-hour observation is absent or its reset has elapsed, while the weekly observation is fresh
- **THEN** the policy state is `data_unavailable`

#### Scenario: Capped account loses standard telemetry

- **GIVEN** an enabled policy has fresh standard observations below its threshold
- **WHEN** a successful poll returns no standard windows
- **THEN** the dashboard reports `data_unavailable`
- **AND** selection rejects the account with `account_usage_limit_reached`

#### Scenario: Post-commit policy read is cancelled

- **GIVEN** a below-limit observation is cached for an enabled policy
- **WHEN** a blocking observation commits and the following policy read is cancelled
- **THEN** local selection inputs are invalidated before cancellation propagates
- **AND** no new work is authorized from the old observation

### Requirement: Enabled usage limits gate account selection

An account whose enabled policy is `reached` or `data_unavailable` MUST be excluded after upstream status, quota, and cooldown checks and before error-backoff classification, sticky affinity, single-account routing, manual routing policy, additional-quota routing, health-tier selection, backoff fallback, fair-share capacity accounting, and every routing strategy. Additional-quota routing MUST NOT bypass the standard policy. Excluded accounts MUST contribute no fair-share stream capacity or lease and key counters. A policy block MUST NOT change the account's persisted upstream status.

When every otherwise eligible candidate is excluded by its policy, including a hard-sticky owner whose peers form a congested pool, selection MUST return `account_usage_limit_reached` with HTTP 429, type `rate_limit_error`, and no upstream reset deadline. It MUST NOT report an upstream rate limit or a fair-share denial, even when other accounts are upstream rate-limited or quota-exceeded. Opportunistic admission MUST NOT rewrite the code to `rate_limit_exceeded`, and the all-blocked path MUST honor caller inputs that disable pool-exhaustion errors or widen the exhaustion-evidence pool. An account that is itself upstream exhausted MUST keep the public `usage_limit_reached` error and its reset metadata. Direct HTTP Responses requests, streaming and nonstreaming, and backend SSE terminal events MUST use the same code and type.

#### Scenario: Another account is selected below the limit

- **GIVEN** account A has reached its maximum usage
- **AND** account B is otherwise eligible and below its maximum
- **WHEN** the proxy selects an account
- **THEN** it selects account B and no fallback selects account A

#### Scenario: Additional quota does not bypass the policy

- **GIVEN** account A has additional quota for the requested model and has reached its standard maximum usage
- **WHEN** gated-model routing ignores standard upstream quota status
- **THEN** account A remains excluded

#### Scenario: Reset usage makes the account eligible again

- **GIVEN** an account was excluded because a window reached its maximum
- **WHEN** a post-reset observation reports every limited window below its threshold
- **THEN** the account becomes eligible without any policy change

#### Scenario: Mixed pools report the local policy error

- **GIVEN** one otherwise eligible account is `reached` or `data_unavailable`
- **AND** another account is upstream rate-limited and therefore not a candidate
- **WHEN** no account can be selected
- **THEN** selection returns `account_usage_limit_reached`

#### Scenario: Hard-sticky owner policy precedes peer fair share

- **GIVEN** a hard-sticky owner is `reached` and the other eligible accounts are congested for the API key
- **WHEN** the proxy re-evaluates the owner
- **THEN** selection returns `account_usage_limit_reached` without waiting for peer capacity
- **AND** the sticky mapping is unchanged

#### Scenario: Upstream exhaustion keeps its public error

- **GIVEN** an account is upstream quota-exceeded with reset metadata and its local policy is also reached
- **WHEN** no account can be selected
- **THEN** the public error remains `usage_limit_reached` with HTTP 429 and the upstream reset metadata

#### Scenario: HTTP Responses request is denied by policy

- **WHEN** a streaming or nonstreaming HTTP Responses request is denied solely by an enabled policy
- **THEN** it returns HTTP 429 with code `account_usage_limit_reached` and type `rate_limit_error`
- **AND** nothing is dispatched upstream

### Requirement: Disabled policies preserve routing-pool semantics

When every policy is disabled, the usage-policy and concurrency-cap projections MUST preserve canonical routing, backoff fallback, and terminal-error semantics. Administratively unavailable and policy-blocked accounts MUST NOT contribute fair-share capacity or become selectable, and the evidence needed for canonical fallback and terminal errors MUST remain available independently of those projections. Opportunistic admission MUST keep its cap-before-upstream-exhaustion error precedence for accounts that no policy blocks, and cap exhaustion MUST NOT mask a policy denial.

#### Scenario: Backoff owner with an excluded peer

- **GIVEN** an active account in error backoff is below its concurrency cap
- **AND** its only peer is paused, or is excluded by public account loading, and every policy is disabled
- **WHEN** the pool is projected for usage policy and concurrency admission
- **THEN** the established backoff fallback behaves as before the feature
- **AND** the peer contributes no fair-share capacity

#### Scenario: Upstream quota evidence is retained

- **GIVEN** a backoff owner has a rate-limited or quota-exceeded peer in the loaded pool
- **WHEN** ordinary or soft-sticky routing evaluates the pool with every policy disabled
- **THEN** the established backoff fallback remains available

### Requirement: Shared transports authorize every new turn

Each new logical HTTP bridge turn, synthetic bridge prewarm, and `response.create` on an existing proxy WebSocket MUST re-evaluate the pinned account's policy before dispatch, including when a bridge keeps or reacquires its stream lease. The final check MUST run after the waits for serialized dispatch, durable continuity preparation, and, on a WebSocket, response-create lease acquisition. A WebSocket MUST also check before lease acquisition so that concurrency exhaustion cannot hide a policy denial. Authorization reads MUST NOT hold the bridge pending-response lock, and each read MUST have a five-second deadline.

A blocking policy MUST reject only the unsent work with `account_usage_limit_reached` and release its queue, waiter, lease, reservation, and gate accounting. Already-dispatched work MUST keep its ownership and settlement. A bridge MUST retire after its admitted turns drain; a WebSocket MUST keep its shared upstream. A failed or timed-out read MUST reject the new work with HTTP 503 `account_usage_limit_authorization_failed` without retiring the transport, and its request log MUST NOT record an upstream status code.

A missing, paused, or deactivated owner, or a `reauth_required` owner whose stored access token is known to be expired, MUST fail the new turn as owner-unavailable (`previous_response_owner_unavailable` on a WebSocket, the bridge continuity-lost response on a bridge) without creating a new lease. A `reauth_required` owner whose token is not known to be expired MUST be evaluated like an active owner. After the final check, a pending upstream reconnect MUST transfer the unsent frame through the existing reconnect path. The sender MUST atomically confirm that it still owns an unexpired pending frame before binding its owner or sending it; it MUST NOT dispatch or settle again a frame the reader already finalized, and MUST release any lease acquired for that frame. A rejected frame MUST keep ownership of its cleanup until settlement completes, and cancellation MUST propagate without abandoning that cleanup.

#### Scenario: Reused bridge owner reaches its limit

- **GIVEN** an HTTP bridge is pinned to an account from an earlier turn
- **AND** the account's policy becomes `reached` or `data_unavailable`
- **WHEN** a new turn reuses the bridge with a retained or released stream lease
- **THEN** the turn fails with `account_usage_limit_reached` before upstream dispatch
- **AND** earlier turns settle normally and the bridge retires after they drain

#### Scenario: Policy changes while a bridge turn waits for dispatch

- **GIVEN** a bridge turn passed queue and response-create admission
- **WHEN** its account becomes usage-limited during the serialized-dispatch wait or durable continuity preparation
- **THEN** the turn fails with `account_usage_limit_reached` without being sent
- **AND** its queue, waiter, and lease accounting are released

#### Scenario: WebSocket policy read fails

- **GIVEN** a proxy WebSocket has an admitted response in flight
- **WHEN** the policy read for a new `response.create` fails or times out
- **THEN** only the new frame fails with `account_usage_limit_authorization_failed` and is not sent
- **AND** the admitted response completes on the same upstream

#### Scenario: Pinned owner becomes unavailable

- **GIVEN** a proxy WebSocket is pinned to an account that is then deleted, paused, deactivated, or `reauth_required` with a known-expired token
- **WHEN** the client sends a new `response.create`
- **THEN** it fails with `previous_response_owner_unavailable` before upstream dispatch
- **AND** admitted work on the socket continues

#### Scenario: Reauthentication warning keeps policy routing

- **GIVEN** a pinned owner becomes `reauth_required` with a token that is not known to be expired
- **WHEN** a new bridge turn or `response.create` is authorized
- **THEN** an `available` policy permits the same owner and a `reached` policy blocks the turn

#### Scenario: Cancellation interrupts rejected-frame cleanup

- **WHEN** a final check rejects an admitted WebSocket frame and cancellation arrives while its account lease is being released
- **THEN** nothing is sent upstream and the frame's lease, reservation, and gate are released
- **AND** overlapping dispatched work keeps its own settlement

### Requirement: Final selection authorization fails closed

Final owner authorization MUST read committed policy, status, and standard telemetry in one database snapshot, independently of the selection cache. It MUST distinguish permission, a policy block, an unavailable owner, and an authorization failure, and it MUST NOT report an unavailable owner as policy-blocked. Peer replicas MUST keep the existing invalidation and TTL fallback; acknowledging a mutation MUST NOT be assumed to invalidate every replica synchronously.

If selection inputs are invalidated during affinity persistence or recovery-probe commitment, selection MUST NOT return the account from that attempt or keep its provisional lease; it MUST retry against fresh inputs within a bounded budget, and a fresh evaluation that finds an owner or policy denial MUST return that error. If they are invalidated during process-seed persistence, selection MUST release its provisional admission and fail closed with `selection_state_changed` before upstream dispatch. If the budget runs out because inputs keep changing, sticky and unbound selection MUST fail closed with the retryable local 503 `selection_state_changed`, even when the owner check would allow the account. Every failed final authorization MUST release provisional leases, token pressure, and recovery probes, MUST NOT publish a new or changed sticky owner, and MUST keep already committed affinity and process-seed owners. A rejected probe admission MUST NOT consume the recovery quiet interval. Cancellation MUST propagate only after this cleanup finishes, including when cancellation repeats during cleanup.

#### Scenario: Usage crosses a threshold while affinity is persisted

- **GIVEN** an account was selected from an available observation
- **WHEN** a committed observation crosses its threshold and invalidates selection inputs during affinity persistence
- **THEN** that attempt's provisional lease and concurrency accounting are released
- **AND** the retried evaluation returns `account_usage_limit_reached` without an account
- **AND** the committed affinity is unchanged

#### Scenario: Selection inputs keep changing through retry exhaustion

- **WHEN** selection inputs change after every bounded selection attempt
- **THEN** selection fails closed with `selection_state_changed` and publishes no new affinity
- **AND** provisional leases and recovery probes are released

#### Scenario: Owner disappears on the final attempt

- **WHEN** the selected owner is deleted or becomes administratively unavailable during the final attempt
- **THEN** selection returns no account or lease and publishes no new sticky owner
- **AND** the error reports owner unavailability rather than a policy block

#### Scenario: Policy changes while probe commitment waits

- **GIVEN** an account has an enabled 20 percent maximum and a fresh 10 percent observation
- **WHEN** the maximum becomes 10 percent and selection inputs are invalidated while recovery-probe commitment waits
- **THEN** the account is not dispatched and its provisional admission is released
- **AND** the rejected attempt does not consume the probe quiet interval

#### Scenario: Policy changes during process-seed persistence

- **WHEN** a required owner's policy becomes blocking and selection inputs are invalidated while the first process seed is persisted
- **THEN** the request fails with `selection_state_changed` before upstream dispatch and its provisional admission is released
- **AND** the committed process seed keeps its owner

#### Scenario: Existing owner on a replica with cached selection inputs

- **GIVEN** the replica's selection cache still holds a disabled policy
- **AND** a blocking policy has committed in the shared database
- **WHEN** the replica authorizes the next dispatch for an existing owner
- **THEN** the authorization read denies the dispatch according to the committed policy

### Requirement: Synthetic warmups respect account usage limits

Every synthetic warmup surface MUST apply the standard usage-limit evaluator before dispatch, and warmup modes and manual force controls MUST NOT bypass it. Reset-confirmed and staggered limit-warmup planning MUST evaluate the refreshed standard observations before creating an attempt. The streaming limit-warmup sender MUST freshly load the account and its current standard observations and evaluate them again immediately before sending. A `reached` or `data_unavailable` result MUST fail the attempt with `account_usage_limit_reached`, and a failed final read MUST fail it with `account_usage_limit_authorization_failed`; neither MAY send the probe. Disabled and `available` policies MUST keep existing warmup behavior. Warmup authorization MUST NOT write the policy or status it read back during claim, request-log, or usage settlement, so a policy acknowledged after that read remains intact. Quota-planner warmup dispatch MUST reuse its resolved dashboard settings snapshot without another settings wait after final owner authorization.

#### Scenario: Secondary usage reaches the limit before a reset-confirmed probe

- **GIVEN** a primary-window reset creates a limit-warmup candidate
- **AND** current secondary usage is at or above the account's enabled maximum
- **WHEN** planning or final sender authorization evaluates the account
- **THEN** no synthetic request is sent
- **AND** an attempt created before the final check fails with `account_usage_limit_reached`

#### Scenario: Operator edits the policy before warmup settlement

- **GIVEN** a warmup authorized against a 20 percent maximum
- **WHEN** the operator saves a 30 percent maximum before the warmup settles
- **THEN** settlement keeps the 30 percent policy

### Requirement: Unavailable telemetry is not a numeric analytics sample

Historical usage calculations MUST drop unavailable measurement placeholders before window functions, deltas, averages, or trends use their values. A genuine zero-percent measurement with valid quota metadata MUST remain a measurement.

#### Scenario: Placeholder between real measurements

- **GIVEN** a window has measurements of 70 and 71 percent with an unavailable placeholder between them
- **WHEN** demand is calculated
- **THEN** the positive usage delta is one percentage point, not 71

### Requirement: Missing short-window telemetry cannot authorize early recovery

When an upstream rate-limit hold has a future deadline, a confirmed long-window reset MUST NOT clear the hold while the latest primary row is an unavailable placeholder and the plan's primary capacity is positive or unknown; known zero primary capacity MUST ignore that placeholder. The latest row of the reset window MUST be a real measurement, so a newer placeholder MUST NOT confirm availability. A real zero-percent measurement with valid quota metadata MUST remain available evidence.

#### Scenario: Long-window reset with a missing short-window measurement

- **GIVEN** a rate-limited paid account has a future deadline tied to its long window
- **AND** the long window has a confirmed reset while the latest primary row is a placeholder
- **WHEN** background recovery evaluates the account
- **THEN** it keeps the hold until valid short-window evidence or the deadline permits recovery

### Requirement: Dashboard shows usage-limit state and reserved quota

Account summaries SHALL expose the saved percentages, the enabled flag, each window's effective threshold, and the evaluated state (`disabled`, `available`, `reached`, or `data_unavailable`). Account cards, lists, and summary counts SHALL show an otherwise active account in state `reached` as `Limit reached` and in state `data_unavailable` as `Usage unavailable`, SHALL show a non-active upstream status instead, and the account list status filter SHALL use the same displayed status. Quota bars SHALL show the reserved share as a hatched segment on the left, usable quota next, and consumed quota on the right, with an accessible label; the reserved share SHALL NOT exceed provider remaining. A percentage beside such a bar SHALL keep the provider-remaining number and take its severity color from usable quota. Usage donuts SHALL show reserved credits as one hatched slice, separate from usable and consumed credits.

After an acknowledged policy mutation, the dashboard SHALL apply the acknowledged policy to every cached account list and SHALL show an enabled policy as `Usage unavailable` until a read returns the evaluated state. A read that started before the mutation, including one whose query has since become inactive, MUST NOT revert the acknowledged policy, and overlapping mutations MUST apply in order.

#### Scenario: Reached policy is distinct from upstream exhaustion

- **GIVEN** an active account has reached its enabled maximum
- **WHEN** it is shown on an account card or list
- **THEN** it displays `Limit reached`, not an upstream quota status

#### Scenario: Upstream status takes precedence

- **GIVEN** an account has reached its enabled maximum and its upstream status is quota exceeded
- **WHEN** it is shown on an account card
- **THEN** it displays `Quota exceeded`

#### Scenario: Remaining quota with a reserve

- **WHEN** usage is 54 percent and the effective threshold is 80 percent
- **THEN** the view shows 46 percent provider remaining, 20 percent reserved, and 26 percent usable

#### Scenario: Refetch fails after a policy change

- **GIVEN** an operator enables or edits a policy and the API acknowledges it
- **WHEN** the following account-list refetch fails
- **THEN** the dashboard shows the acknowledged policy as `Usage unavailable`
- **AND** it does not keep a stale off or `Active` state

#### Scenario: Inactive read settles after a save

- **GIVEN** an account read starts before a policy mutation and its query then becomes inactive
- **WHEN** the mutation is acknowledged and the older read settles
- **THEN** the cached policy still reflects the acknowledged mutation

### Requirement: Reserve-oriented editing
The editor SHALL ask how much quota to keep for direct use, converting reserve percentages to the existing maximum-used API contract. Zero reserve SHALL map to 100 percent maximum-used; the input's declared maximum MUST exclude 100 percent reserve because the API requires a positive maximum-used value. The editor SHALL show one row per reported 5-hour or weekly window, holding the reserve that applies to that window: its override, otherwise the default. It SHALL add one row for the default when window telemetry is unknown or no standard window is reported (labeled All windows, or Monthly for a monthly-only account), or when a monthly, nonstandard, or unidentified window is reported alongside a standard window (labeled Other windows). The editor MUST NOT offer a 5-hour or weekly row when that window is absent, and monthly usage MUST NOT use a weekly override.
Each row SHALL show provider remaining and the quota Codex LB could still use with the unsaved reserve, and SHALL mark a window as reached before saving when its remaining quota is at or below the reserve. A disabled policy SHALL show the full remaining quota as usable.
A blank row SHALL mean no reserve. On save, a standard row equal to the default it would inherit SHALL clear its override; any other value SHALL be stored as that window's override. When no default row is shown, the stored default MUST be retained. Hiding an inapplicable override MUST preserve its saved value when another row is edited. Saving with no reserve in every shown row SHALL remove the policy. Save and Cancel SHALL appear only while the draft differs from the saved policy, and Escape SHALL discard the draft; a saved policy with no reserve in any shown row SHALL instead offer removal without an edit. An edited reserve outside the range from 0 up to but excluding 100 percent SHALL show inline range feedback and SHALL prevent saving; validation SHALL apply only to edited rows so an untouched row keeps its exact saved value. The enable switch SHALL be visible in every state, SHALL be unavailable until a policy is saved, and SHALL apply immediately. Saving a new policy SHALL enable it. The editor SHALL label an enabled policy in state `reached` or `data_unavailable` with `Limit reached` or `Usage unavailable` and SHALL NOT show a separate active-state badge.
Bars SHALL place reserved quota on the left, usable quota next, and consumed quota on the right. Stripes SHALL not exceed provider remaining.

#### Scenario: Twenty percent reserve
- **WHEN** the operator saves a 20 percent reserve and usage is 54 percent
- **THEN** the maximum-used API value SHALL be 80
- **AND** the bar SHALL show 20 percent striped on the left, 26 percent usable next, and 54 percent consumed on the right.

#### Scenario: Unsaved reserve previews a blocked window
- **GIVEN** an enabled policy and a 5-hour window with 35 percent remaining
- **WHEN** the operator types a 40 percent 5-hour reserve without saving
- **THEN** the 5-hour row SHALL show 0 percent usable and mark the window as reached.

#### Scenario: Rows show effective reserves
- **GIVEN** a default of 80, a 5-hour override of 70, and a weekly override of 90 on an account reporting 5-hour and weekly windows
- **THEN** the editor SHALL show a 30 percent 5-hour reserve and a 10 percent weekly reserve and no default row
- **WHEN** the operator changes the weekly reserve to 15 and saves
- **THEN** the update SHALL set the 5-hour override to 70 and the weekly override to 85 without changing the default.

#### Scenario: Window matching the default keeps inheriting
- **GIVEN** a default of 80 and no overrides on an account reporting 5-hour and weekly windows
- **WHEN** the operator changes only the 5-hour reserve to 30 and saves
- **THEN** the update SHALL set the 5-hour override to 70 and clear the weekly override.

#### Scenario: Clearing every row removes the policy
- **WHEN** the operator clears every shown reserve of a saved policy and saves
- **THEN** the policy SHALL be removed and disabled.

#### Scenario: Policy left with only an unreported-window override
- **GIVEN** an enabled policy whose only value is a 5-hour override on an account that reports only a weekly window
- **THEN** the editor SHALL offer removal without an edit
- **AND** removing it SHALL clear every value and disable the policy.

#### Scenario: Reserve controls follow reported windows
- **GIVEN** an account reports only a weekly standard window
- **THEN** the editor SHALL offer only a weekly row, and saving it SHALL retain the stored default and any hidden 5-hour override.
- **GIVEN** an account reports only a monthly standard window
- **THEN** the editor SHALL offer a Monthly row backed by the default without 5-hour or weekly rows.
- **WHEN** the operator saves, disables, or re-enables that policy
- **THEN** the monthly effective limit SHALL retain the same default-policy semantics.

#### Scenario: Nonstandard windows do not offer standard overrides
- **WHEN** reported windows have durations other than 300 or 10080 minutes
- **THEN** the editor SHALL offer only an All windows row for the default
- **AND** a 10080-minute window in either reported slot SHALL offer a weekly row.

## MODIFIED Requirements

### Requirement: Foreground routing treats local usage snapshots as non-authoritative

Unless an enabled account usage-limit policy explicitly blocks the account, foreground proxy account selection MUST NOT reject an otherwise active account solely because local standard usage snapshots, synthetic planner costs, or inferred budget pressure report that the account has reached or exceeded 100 percent usage. Such local usage data MAY influence ranking, health/drain decisions, opportunistic burn policy, dashboards, and diagnostics, but it MUST NOT be reported as upstream rate limiting and MUST NOT produce `no_accounts` before an upstream attempt when no explicit local policy or local capacity guard is exhausted.

#### Scenario: Active account at local primary usage exhaustion is still selectable

- **GIVEN** an upstream account is persisted as active
- **AND** its account usage-limit policy is disabled
- **AND** its latest local primary usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates the account
- **THEN** the account remains eligible for upstream routing
- **AND** the selection result does not report a local `Rate limit exceeded` or `no_accounts` failure

#### Scenario: Active account at local secondary usage exhaustion is still selectable

- **GIVEN** an upstream account is persisted as active
- **AND** its account usage-limit policy is disabled
- **AND** its latest local secondary usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates the account
- **THEN** the account remains eligible for upstream routing
- **AND** the local secondary usage snapshot is not promoted into a persisted upstream quota-exceeded state before an upstream response proves quota exhaustion

#### Scenario: Advisory usage reset is not persisted as an account block

- **GIVEN** an upstream account is persisted as active
- **AND** its account usage-limit policy is disabled
- **AND** its latest local usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates and persists selection state for the active account
- **THEN** the account-level blocking reset remains unset
- **AND** a later upstream rate-limit response without reset metadata is governed by upstream retry/backoff cooldown rather than the advisory usage reset
