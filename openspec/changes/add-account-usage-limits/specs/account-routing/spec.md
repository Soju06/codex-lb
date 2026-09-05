# account-routing

## ADDED Requirements

### Requirement: Final owner authorization is explicit and fail-closed

Fresh owner authorization MUST distinguish permission, a local usage-policy block, an unavailable owner, and an authorization infrastructure failure. A missing, paused, deactivated, or reauthentication-required owner MUST NOT be admitted on retry exhaustion. Failed final selection authorization MUST release provisional leases and recovery probes and MUST NOT publish a new or changed sticky owner. An unavailable owner MUST NOT be reported as having reached its usage policy. Cancellation MUST propagate after provisional resource cleanup.

#### Scenario: Owner disappears on the final selection attempt

- **GIVEN** selection state is invalidated on every bounded selection attempt
- **AND** the selected owner is deleted or becomes administratively unavailable during the final attempt
- **WHEN** final fresh authorization runs
- **THEN** selection returns no account or lease
- **AND** no new sticky owner is published and no provisional runtime pressure remains
- **AND** the error identifies owner unavailability rather than a usage-policy block


#### Scenario: Repeated cancellation interrupts final authorization cleanup

- **GIVEN** selection owns a provisional stream lease and estimated-token pressure
- **WHEN** final authorization is cancelled and another cancellation arrives while resource release awaits its runtime lock
- **THEN** cleanup finishes releasing provisional lease and probe ownership before cancellation propagates
- **AND** no sticky owner is published and no stream or token pressure remains


### Requirement: Disabled policies preserve routing-pool semantics

When all usage policies are disabled, applying the usage-policy and concurrency-cap projections MUST preserve established canonical routing, backoff fallback, and terminal-error semantics. Administratively unavailable and usage-policy-blocked accounts MUST NOT contribute fair-share capacity or become selectable. Evidence needed for canonical fallback and terminal errors MUST remain available independently of those capacity projections.

#### Scenario: A canonical pool contains a backoff owner and a paused peer

- **GIVEN** an active account in error backoff is below its concurrency cap
- **AND** its only peer is paused and all usage policies are disabled
- **WHEN** the canonical pool is projected for usage policy and concurrency admission
- **THEN** the established controlled backoff fallback remains available
- **AND** the paused peer contributes no fair-share capacity

#### Scenario: Public routing preserves pre-feature administrative filtering

- **GIVEN** one backoff owner has a persisted paused or deactivated peer that public account loading excludes
- **WHEN** ordinary or soft-sticky routing evaluates the loaded pool with usage policies disabled
- **THEN** the excluded peer does not newly manufacture backoff fallback

#### Scenario: Public routing retains upstream quota block evidence

- **GIVEN** one backoff owner has a rate-limited or quota-exceeded peer that remains in the loaded pool
- **WHEN** ordinary or soft-sticky routing evaluates the pool with usage policies disabled
- **THEN** the established controlled backoff fallback remains available

### Requirement: Unavailable telemetry is not a numeric analytics sample

Historical usage calculations MUST exclude unavailable measurement placeholders before window functions, deltas, averages, or trends use their values. A genuine zero-percent measurement with valid quota metadata MUST remain a measurement.

#### Scenario: Missing observation between real measurements

- **GIVEN** one quota window has real measurements of 70 and 71 percent with an unavailable placeholder between them
- **WHEN** demand is calculated over those observations
- **THEN** the measured positive usage delta is one percentage point, not 71

### Requirement: Authorization failures retain local error provenance

An `account_usage_limit_authorization_failed` error generated without an upstream response MUST NOT produce an upstream HTTP status in request logs.

#### Scenario: Database authorization read fails

- **GIVEN** a final local owner-authorization read fails before dispatch
- **WHEN** the proxy records the resulting failure
- **THEN** the request log contains the local authorization error
- **AND** its upstream status code is absent

### Requirement: Acknowledged policy mutations cannot be reverted by older reads

Dashboard policy reconciliation MUST prevent an account or dashboard read started before an acknowledged mutation from replacing the acknowledged policy with older state, including when the read becomes inactive. Overlapping policy mutations MUST preserve their acknowledged ordering and MUST NOT allow a delayed prior reconciliation to revert a later acknowledged policy.

#### Scenario: Inactive dashboard read settles after save

- **GIVEN** a dashboard read starts before a policy mutation and subsequently becomes inactive
- **WHEN** the mutation is acknowledged and the older read then settles
- **THEN** cached policy fields still reflect the acknowledged mutation or a newer authoritative read
- **AND** they do not revert to the pre-mutation policy

### Requirement: Accounts have a reversible maximum-usage policy

Each account SHALL support an optional maximum standard-quota used percentage greater than 0 and at most 100, plus an enabled state. The policy SHALL default to disabled for existing and new accounts. Disabling a configured policy SHALL retain its percentage for later re-enablement, while removing the policy SHALL clear the percentage and disable it. For a disabled update, the API MUST retain the latest stored percentage when the percentage field is omitted, clear it when the field is explicitly `null`, and replace it when a numeric value is supplied. The API MUST reject an enabled policy without an explicitly supplied percentage, MUST reject an enabled policy with a `null` percentage, and MUST reject percentages outside the supported range.

#### Scenario: Operator temporarily disables a configured limit

- **GIVEN** an account has an enabled maximum usage of 10 percent
- **WHEN** the operator disables the policy without removing it
- **THEN** the account retains 10 percent as its configured value
- **AND** routing does not apply that policy until it is re-enabled

#### Scenario: Stale dashboard disables without reverting a newer percentage

- **GIVEN** a dashboard loaded a configured maximum of 10 percent
- **AND** another client changes the stored maximum to 20 percent
- **WHEN** the stale dashboard disables the policy while omitting the percentage field
- **THEN** the policy is disabled
- **AND** the stored maximum remains 20 percent

#### Scenario: Operator removes a configured limit

- **GIVEN** an account has a configured maximum usage
- **WHEN** the operator removes the policy
- **THEN** the percentage is cleared
- **AND** the policy is disabled

### Requirement: Account usage limits are hard routing eligibility gates

For an account with an enabled maximum usage policy, the selector MUST evaluate current standard primary and long-window quota observations after normalizing weekly-only and monthly-only account shapes. When historical monthly and normalized weekly-only shapes coexist, observations from fetches separated by more than the shared sibling-fetch margin MUST be ordered by `recorded_at`; observations within the margin MUST use quota metadata and reset-deadline precedence, with the weekly-primary shape winning an otherwise exact tie. If any current standard window reports used percentage greater than or equal to the configured maximum, the account MUST be excluded after upstream status, quota, and cooldown checks but before error-backoff classification, sticky affinity, single-account routing, manual routing policy, additional-quota routing, health-tier selection, backoff fallback, fair-share capacity accounting, or any routing strategy is applied. Standard usage limits MUST NOT be bypassed by an additional-quota request that ignores standard upstream exhaustion. Reaching a local account policy MUST NOT mutate the account's persisted upstream status.

Each newly admitted logical HTTP bridge turn MUST re-evaluate its continuity-pinned account through the same standard usage-limit policy, including when a reused bridge retains its stream lease and when an idle bridge would otherwise reacquire that lease. A policy denial MUST occur before the new turn is queued or sent, MUST use the `account_usage_limit_reached` response contract, and MUST retire the bridge after already-admitted turns drain without rebinding or disrupting their ownership and settlement. If the pinned account no longer exists or becomes administratively unavailable, admission MUST fail closed with the established bridge continuity-lost response and retire the bridge without creating a new runtime lease for that owner.
If the final direct owner-policy snapshot read fails, the new turn MUST fail closed with `account_usage_limit_authorization_failed` before upstream dispatch without retiring the bridge. Cancellation MUST continue to propagate.

Each newly admitted `response.create` on an existing proxy WebSocket MUST re-evaluate the socket-pinned account through the same standard usage-limit policy. A `reached` or `data_unavailable` result MUST reject only the new frame with `account_usage_limit_reached` before upstream dispatch, without disrupting already-admitted responses on the shared socket.
If the final policy read fails, the new frame MUST fail closed with `account_usage_limit_authorization_failed` before upstream dispatch, without retiring the shared upstream or disrupting already-admitted responses. Cancellation MUST continue to propagate.

#### Scenario: Equality reaches the limit

- **GIVEN** account A has an enabled maximum usage of 10 percent
- **AND** its current weekly usage observation is exactly 10 percent used
- **WHEN** any proxy route selects an account
- **THEN** account A is not eligible

#### Scenario: Another account is selected below the limit

- **GIVEN** account A has reached its enabled maximum usage
- **AND** account B is otherwise eligible and below its enabled maximum usage
- **WHEN** the proxy selects an account
- **THEN** it selects account B
- **AND** no fallback selects account A

#### Scenario: Additional quota does not bypass the standard account policy

- **GIVEN** account A has available additional quota for the requested model
- **AND** account A has reached its enabled standard maximum usage
- **WHEN** gated-model routing ignores standard upstream quota status
- **THEN** account A remains excluded by the operator's maximum-usage policy

#### Scenario: Reset usage makes the account eligible again

- **GIVEN** an account was excluded because a standard window reached its maximum usage
- **WHEN** a current post-reset usage observation reports every available standard window below the maximum
- **THEN** the account becomes eligible without changing or removing the policy

#### Scenario: Reused bridge owner reaches its local limit

- **GIVEN** an HTTP bridge is continuity-pinned to an account from an earlier admitted turn
- **AND** the account's enabled policy becomes `reached` or `data_unavailable`
- **WHEN** a new logical turn reuses the bridge with either a retained or released stream lease
- **THEN** the new turn fails with `account_usage_limit_reached` before upstream dispatch
- **AND** already-admitted work remains pinned and settles normally
- **AND** the bridge retires after that work drains

#### Scenario: Reused bridge policy authorization fails

- **GIVEN** an HTTP bridge is continuity-pinned to an account
- **AND** the final usage-limit policy read for a new logical turn fails
- **WHEN** the turn is authorized
- **THEN** the turn fails with `account_usage_limit_authorization_failed`
- **AND** the turn is not sent upstream
- **AND** the bridge remains available for a later retry

#### Scenario: Reused WebSocket owner becomes administratively unavailable

- **GIVEN** an existing proxy WebSocket is pinned to an account
- **AND** that account is deleted, paused, deactivated, or requires reauthentication
- **WHEN** the client submits a new `response.create` frame
- **THEN** the new turn fails with `previous_response_owner_unavailable` before upstream dispatch
- **AND** already-admitted work on the socket remains uninterrupted

#### Scenario: Reused WebSocket policy authorization fails

- **GIVEN** an existing proxy WebSocket has an already-admitted response in flight
- **AND** the final usage-limit policy read for a new `response.create` fails
- **WHEN** the new frame is authorized
- **THEN** only the new frame fails with `account_usage_limit_authorization_failed`
- **AND** the new frame is not sent upstream
- **AND** the already-admitted response completes without the shared upstream being retired

#### Scenario: Fresh weekly shape supersedes elapsed monthly telemetry

- **GIVEN** a monthly-capable account has an old elapsed monthly observation
- **AND** a genuinely later weekly-only primary observation reports usage below the configured maximum
- **WHEN** the selector evaluates the account
- **THEN** it uses the fresh normalized weekly shape
- **AND** the historical monthly row does not keep the policy in `data_unavailable`

#### Scenario: Upstream exhaustion retains its public error

- **GIVEN** an account is upstream rate-limited or quota-exceeded with 100 percent usage and reset metadata
- **AND** its local maximum usage policy is also reached
- **WHEN** no account can be selected
- **THEN** the public error remains `usage_limit_reached` with HTTP 429
- **AND** the upstream reset metadata is preserved

#### Scenario: Mixed pools report the local policy error

- **GIVEN** one otherwise eligible account has usage-limit state `reached` or `data_unavailable`
- **AND** a different account is upstream rate-limited or quota-exceeded and therefore not a selection candidate
- **WHEN** no account can be selected
- **THEN** selection returns stable error code `account_usage_limit_reached`
- **AND** it does not replace that error with the upstream exhaustion envelope of the non-candidate account

### Requirement: Enabled account usage limits fail closed without current data

An enabled maximum-usage policy MUST require current standard quota data. Elapsed window rows MUST NOT count as exhaustion evidence, but if no current relevant standard observation remains, or a relevant observation is stale or lacks a used percentage, the account MUST be excluded with policy state `data_unavailable`. When all otherwise eligible candidates are excluded by `reached` or `data_unavailable` usage-limit state, selection MUST return stable error code `account_usage_limit_reached` and MUST NOT report the accounts as upstream rate-limited.

#### Scenario: Missing observations preserve the account quota

- **GIVEN** an account has an enabled maximum usage policy
- **AND** no current standard usage observation is available
- **WHEN** the selector evaluates the account
- **THEN** the account is not selected
- **AND** its usage-limit state is `data_unavailable`

#### Scenario: Enabling a policy overlaps a usage refresh

- **GIVEN** an account has a disabled maximum usage policy and an older below-limit observation
- **AND** an in-flight refresh receives a newer standard observation that is at the limit or unavailable
- **WHEN** the operator enables the policy before that refresh commits
- **THEN** the newer observation supersedes the older observation
- **AND** cached selection state is invalidated after the refresh commit
- **AND** the account is not selected

#### Scenario: All accounts are locally capped

- **GIVEN** every otherwise eligible account has limit state `reached` or `data_unavailable`
- **WHEN** the proxy attempts selection
- **THEN** no account is selected
- **AND** the routing error code is `account_usage_limit_reached`

#### Scenario: Locally capped accounts do not enlarge fair-share capacity

- **GIVEN** one or more accounts are excluded by their maximum usage policy
- **WHEN** API-key fair-share admission computes pool capacity and in-flight ownership
- **THEN** those accounts contribute neither stream capacity nor lease/key counters
- **AND** an entirely locally capped pool returns `account_usage_limit_reached` rather than a fair-share denial

#### Scenario: Hard-sticky owner policy takes precedence over peer-pool fair share

- **GIVEN** a hard-sticky conversation owner has usage-limit state `reached` or `data_unavailable`
- **AND** other policy-eligible accounts form a congested pool for the requesting API key
- **WHEN** the proxy re-evaluates the hard-pinned owner
- **THEN** selection returns `account_usage_limit_reached`
- **AND** it does not return `api_key_stream_fair_share` or wait for peer-pool congestion to clear
- **AND** the sticky mapping remains unchanged

#### Scenario: Opportunistic admission preserves the local policy error

- **GIVEN** all otherwise opportunistic-eligible accounts are excluded by their maximum usage policy
- **WHEN** a public route performs opportunistic admission precheck
- **THEN** the response retains code `account_usage_limit_reached`
- **AND** the precheck does not rewrite it to `rate_limit_exceeded`

### Requirement: Dashboard account controls expose usage-limit state and precision

Account summaries SHALL expose the configured percentage, enabled flag, and evaluated state (`disabled`, `available`, `reached`, or `data_unavailable`). The Accounts dashboard SHALL allow an operator to set, edit, enable, disable, and remove the policy. Dashboard account card and list surfaces SHALL display `Limit reached` for an otherwise active account in state `reached` and `Usage unavailable` for an otherwise active account in state `data_unavailable`, without masking a non-active upstream account status. The editable value and maximum-used summary MUST preserve every API-valid persisted numeric percentage without rounding it to a different value. Invalid percentage values MUST receive clear inline range feedback. The dashboard SHALL describe a value of 10 percent as a maximum of 10 percent used (90 percent reserved) and SHALL warn that delayed upstream observations or already in-flight requests can move actual usage past the displayed percentage before the gate observes it.

#### Scenario: Enabled limit is visible and toggleable

- **GIVEN** an account has a configured 10 percent maximum that is disabled
- **WHEN** the operator views the account
- **THEN** the dashboard shows 10 percent maximum used and 90 percent reserved
- **AND** the operator can enable it without re-entering the value

#### Scenario: Reached policy is distinguishable from upstream exhaustion

- **GIVEN** an active account has reached its enabled maximum usage
- **WHEN** the account is shown in the dashboard
- **THEN** dashboard account card and list surfaces display `Limit reached`
- **AND** it does not relabel the persisted account status as upstream quota exhausted

#### Scenario: Unavailable usage is visibly blocked

- **GIVEN** an active account has an enabled maximum whose usage data is unavailable
- **WHEN** the account is shown in the dashboard
- **THEN** dashboard account card and list surfaces display `Usage unavailable`
- **AND** they do not display the account as `Active`

#### Scenario: Evaluated-state refetch fails after enabling or editing a limit

- **GIVEN** an operator successfully enables or edits an account usage limit
- **AND** the required account-list refetch fails
- **WHEN** the dashboard applies the successful mutation response
- **THEN** the dashboard displays the enabled policy as `Usage unavailable`
- **AND** it does not preserve a stale `Off` or `Active` state

#### Scenario: Upstream account status takes precedence

- **GIVEN** an account has reached its enabled maximum usage
- **AND** its persisted upstream status is not active
- **WHEN** the account is shown in the main dashboard
- **THEN** its account card displays the upstream account status instead of `Limit reached`

#### Scenario: Fractional configured precision remains editable

- **GIVEN** the API returns a configured maximum of 0.001 or 99.999 percent
- **WHEN** the operator views or edits the policy
- **THEN** the dashboard shows the same configured numeric value
- **AND** saving an edit does not first quantize the persisted value to 0 or 100

#### Scenario: Invalid percentage receives range feedback

- **GIVEN** the operator enters a percentage that is not greater than 0 and no more than 100
- **WHEN** the value is validated
- **THEN** the dashboard displays inline range feedback associated with the input
- **AND** saving remains unavailable

### Requirement: Synthetic warmups respect account usage limits

Every synthetic warmup surface MUST apply the canonical standard-window usage-limit policy before dispatch. An enabled policy in state `reached` or `data_unavailable`, or a failed final authorization read, MUST fail closed without upstream traffic. Warmup mode and manual force controls MUST NOT bypass this operator policy.

Reset-confirmed and staggered limit-warmup planning MUST apply the canonical standard usage-limit evaluator to the refreshed standard observations before creating an attempt. The streaming limit-warmup sender MUST freshly load the account and its current standard primary, secondary, and monthly observations and MUST reapply the evaluator immediately before sending upstream traffic. A `reached` or `data_unavailable` result MUST fail the attempt with code `account_usage_limit_reached` and MUST NOT send the probe. A final authorization read failure MUST fail closed with code `account_usage_limit_authorization_failed` and MUST NOT send the probe. Disabled and `available` policies MUST preserve existing limit-warmup behavior.

#### Scenario: Missing current data blocks warmup

- **GIVEN** an account has an enabled maximum usage policy
- **AND** its current standard usage data is unavailable
- **WHEN** warmup planning or execution evaluates the account
- **THEN** no synthetic warmup is planned or sent

#### Scenario: Secondary usage reaches the limit before a reset-confirmed probe

- **GIVEN** a primary-window reset creates a limit-warmup candidate
- **AND** the account has an enabled maximum usage policy
- **AND** its current secondary-window usage is at or above that maximum
- **WHEN** limit-warmup planning or final sender authorization evaluates the account
- **THEN** no synthetic upstream request is sent
- **AND** an attempt created before the final authorization fails with code `account_usage_limit_reached`

#### Scenario: Available and disabled policies preserve warmup behavior

- **GIVEN** an otherwise eligible short-window account has an `available` or disabled usage-limit policy
- **WHEN** warmup planning and execution evaluate the account
- **THEN** the usage-limit gate does not prevent its normal warmup action


### Requirement: Policy visibility follows explicit observation boundaries

The usage-policy API MUST commit the acknowledged configuration and invalidate
its local selection inputs before returning success. Existing-owner dispatch
checks MUST read authoritative policy/status independently of that cache. Peer
fresh selection MUST retain the existing invalidation and TTL fallback contract;
it MUST NOT assume that acknowledging an API mutation synchronously invalidates
every replica. Already dispatched work MUST retain its settlement ownership.

#### Scenario: An existing owner is used on a replica with cached selection inputs

- **GIVEN** the replica's selection cache still contains a disabled policy
- **AND** an enabled blocking policy has committed in the shared database
- **WHEN** the replica performs its next existing-owner dispatch authorization read
- **THEN** that read denies the new dispatch according to the committed policy
- **AND** it does not substitute the cached disabled policy for authorization
