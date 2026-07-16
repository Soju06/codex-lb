## ADDED Requirements

### Requirement: Force Probe settles replica-local probing health

After the operator Force Probe request and its immediate usage refresh complete, the system MUST report the result to the process-local load balancer. Before settling an HTTP 2xx response, the load balancer MUST reload the refreshed standard usage rows and apply the same elapsed-window, weekly-only-primary, zero-primary-capacity, plan-applicable monthly-window, and long-window normalization used by ordinary account selection. The settlement MUST apply only when replica-local runtime state has not changed since snapshot loading began, so a newer failure or other health observation cannot be cleared by an older probe result. An accepted 2xx settlement MUST count as one successful probe observation, clear replica-local transient error state, and advance the existing fixed probe-success state machine only when the normalized status and usage remain eligible to probe. Reaching the fixed success-streak requirement MUST return the account to `HEALTHY` routing.

A non-2xx upstream response or the network-failure sentinel MUST NOT count as a successful observation and MUST reset an in-progress probe-success streak. Probe settlement MUST NOT override a persisted hard-blocked status or usage condition, invent a persistent account error, or change the Force Probe response schema.

#### Scenario: Successful Force Probes rehabilitate a probing account

- **GIVEN** an active account is in the process-local probing tier with usage below the fixed drain thresholds
- **WHEN** Force Probe receives HTTP 2xx enough consecutive times to meet the fixed success-streak requirement
- **THEN** each response contributes one successful probe observation
- **AND** the account returns to the healthy tier on that replica

#### Scenario: Rejected Force Probe cannot restore health

- **GIVEN** an account is in the process-local probing tier with an in-progress success streak
- **WHEN** Force Probe receives HTTP 400 or another non-2xx response
- **THEN** the response does not count as a success
- **AND** the in-progress success streak is reset
- **AND** the account does not become healthy from that result

#### Scenario: Usage pressure still prevents recovery

- **GIVEN** Force Probe receives HTTP 2xx for an account whose refreshed usage remains at or above a fixed drain threshold
- **WHEN** the result is settled into process-local health
- **THEN** the account remains draining rather than becoming probing or healthy

#### Scenario: Monthly usage participates in Force Probe settlement

- **GIVEN** a plan whose applicable long window is monthly and whose refreshed monthly usage is at the fixed long-window drain threshold
- **WHEN** Force Probe receives HTTP 2xx and settles local health
- **THEN** the monthly row is normalized as the effective long window
- **AND** the account remains draining

#### Scenario: Weekly-only primary is not treated as a short window

- **GIVEN** an account whose refreshed weekly-only usage is stored in the primary slot above the short-window drain threshold but below the long-window drain threshold
- **WHEN** Force Probe receives HTTP 2xx and settles local health
- **THEN** that row is normalized into the long window
- **AND** it does not drain the account as short-window usage

#### Scenario: Zero-capacity primary row does not drain a free account

- **GIVEN** an active free-plan account has a stored primary row above the short-window drain threshold
- **AND** the plan has zero primary-window capacity while applicable monthly usage remains healthy
- **WHEN** Force Probe receives HTTP 2xx and settles local health
- **THEN** the primary row is excluded from health-tier evaluation
- **AND** the successful probe can advance recovery instead of returning the account to draining

#### Scenario: Newer failure wins over in-flight Force Probe success

- **GIVEN** Force Probe has begun loading an account and its refreshed usage for a successful result
- **AND** the replica records a newer upstream failure before probe settlement
- **WHEN** the older successful probe attempts to settle
- **THEN** settlement is rejected as stale
- **AND** the newer transient error state and reset success streak remain intact
