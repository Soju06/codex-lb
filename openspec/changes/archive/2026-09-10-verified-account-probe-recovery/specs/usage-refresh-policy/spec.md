## MODIFIED Requirements

### Requirement: Operators can probe an account to wake the upstream limiter

The dashboard MUST expose an admin-only endpoint that sends a single minimal `responses.create` directly to upstream pinned to one account, bypassing load-balancer scoring, then immediately refreshes that account's `/wham/usage` snapshot. The probe `responses.create` MUST set `max_output_tokens` to `16`, the current Codex token floor; values below that floor MUST NOT be used. The endpoint MUST surface the before/after usage and account status, plus `probeCompleted` and `holdRecovered`, so operators can distinguish HTTP acceptance, completed execution and persisted recovery. When the requested model is omitted, a known held model SHALL be used; otherwise the existing default model SHALL apply. A matching held service tier SHALL be sent with the probe. Concurrent account probes MUST admit at most one provider request under a bounded renewable claim; a competing request MUST return `409 account_not_probable`.

#### Scenario: Probe wakes the upstream limiter and refreshes usage state
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** the account is `active`, `rate_limited`, or `quota_exceeded`
- **THEN** the service sends one `responses.create` request directly to `{upstream_base_url}/codex/responses` with `max_output_tokens=16`, `stream=true`, `store=false`
- **AND** the service triggers an immediate `UsageUpdater.refresh_accounts` for that account
- **AND** the response body carries `probeStatusCode`, `probeCompleted`, `holdRecovered`, `primaryUsedPercentBefore`, `primaryUsedPercentAfter`, `secondaryUsedPercentBefore`, `secondaryUsedPercentAfter`, `accountStatusBefore`, `accountStatusAfter`

#### Scenario: Probe rejects hard-blocked accounts
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** the account `status` is `paused` or `deactivated`
- **THEN** the endpoint responds `409` with code `account_not_probable`
- **AND** no upstream request is sent

#### Scenario: Dashboard exposes Force probe only for probeable statuses

- **WHEN** the dashboard renders account actions for an account
- **AND** the account `status` is `active`, `rate_limited`, or `quota_exceeded`
- **THEN** the dashboard exposes a Force probe action for that account
- **AND** invoking the action refreshes the account list, dashboard overview, projections, and that account's trends
- **BUT WHEN** the account `status` is `paused` or `deactivated`
- **THEN** the Force probe action is disabled or hidden

#### Scenario: Probe returns 404 for unknown account
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** no account with that id exists
- **THEN** the endpoint responds `404` with code `account_not_found`

### Requirement: Force Probe settles replica-local probing health

After the operator Force Probe request and its immediate usage refresh complete, the system MUST report the result to the process-local load balancer. Before settling an HTTP 2xx response with valid completed execution, the load balancer MUST reload the refreshed standard usage rows and apply the same elapsed-window, weekly-only-primary, zero-primary-capacity, plan-applicable monthly-window, and long-window normalization used by ordinary account selection. The settlement MUST apply only when replica-local runtime state has not changed since snapshot loading began, so a newer failure or other health observation cannot be cleared by an older probe result. An accepted completed-execution settlement MUST count as one successful probe observation, clear replica-local transient error state, and advance the existing fixed probe-success state machine only when the normalized status and usage remain eligible to probe. Reaching the fixed success-streak requirement MUST return the account to `HEALTHY` routing.

An incomplete or invalid stream, a non-2xx upstream response or the network-failure sentinel MUST NOT count as a successful observation and MUST reset an in-progress probe-success streak. Advisory settlement MUST NOT override a persisted hard-blocked status or usage condition or invent a persistent account error. Explicit persisted-hold recovery SHALL follow the completed operator probe recovery contract in account-routing independently of advisory health settlement.

#### Scenario: Successful Force Probes rehabilitate a probing account

- **GIVEN** an active account is in the process-local probing tier with usage below the fixed drain thresholds
- **WHEN** Force Probe receives HTTP 2xx with valid completed execution enough consecutive times to meet the fixed success-streak requirement
- **THEN** each response contributes one successful probe observation
- **AND** the account returns to the healthy tier on that replica

#### Scenario: Rejected Force Probe cannot restore health

- **GIVEN** an account is in the process-local probing tier with an in-progress success streak
- **WHEN** Force Probe receives HTTP 400 or another non-2xx response
- **THEN** the response does not count as a success
- **AND** the in-progress success streak is reset
- **AND** the account does not become healthy from that result

#### Scenario: Usage pressure still prevents recovery

- **GIVEN** Force Probe receives HTTP 2xx with valid completed execution for an account whose refreshed usage remains at or above a fixed drain threshold
- **WHEN** the result is settled into process-local health
- **THEN** the account remains draining rather than becoming probing or healthy

#### Scenario: Monthly usage participates in Force Probe settlement

- **GIVEN** a plan whose applicable long window is monthly and whose refreshed monthly usage is at the fixed long-window drain threshold
- **WHEN** Force Probe receives HTTP 2xx with valid completed execution and settles local health
- **THEN** the monthly row is normalized as the effective long window
- **AND** the account remains draining

#### Scenario: Weekly-only primary is not treated as a short window

- **GIVEN** an account whose refreshed weekly-only usage is stored in the primary slot above the short-window drain threshold but below the long-window drain threshold
- **WHEN** Force Probe receives HTTP 2xx with valid completed execution and settles local health
- **THEN** that row is normalized into the long window
- **AND** it does not drain the account as short-window usage

#### Scenario: Zero-capacity primary row does not drain a free account

- **GIVEN** an active free-plan account has a stored primary row above the short-window drain threshold
- **AND** the plan has zero primary-window capacity while applicable monthly usage remains healthy
- **WHEN** Force Probe receives HTTP 2xx with valid completed execution and settles local health
- **THEN** the primary row is excluded from health-tier evaluation
- **AND** the successful probe can advance recovery instead of returning the account to draining

#### Scenario: Newer failure wins over in-flight Force Probe success

- **GIVEN** Force Probe has begun loading an account and its refreshed usage for a successful result
- **AND** the replica records a newer upstream failure before probe settlement
- **WHEN** the older successful probe attempts to settle
- **THEN** settlement is rejected as stale
- **AND** the newer transient error state and reset success streak remain intact
