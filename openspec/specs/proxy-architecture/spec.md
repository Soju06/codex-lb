# proxy-architecture Specification

## Purpose
Structural fitness gates for the proxy: ProxyService stays a stable façade and internal decomposition (selection orchestration, bridge mixins) cannot drift behavior or re-grow god-modules.
## Requirements
### Requirement: Proxy architecture fitness gates are enforced

The repository SHALL enforce every accepted proxy architecture threshold during
the required lint gate. The complete normative threshold set SHALL be defined
exactly once in the marked machine-readable TOML block below, and
`scripts/check_proxy_architecture.py` SHALL load that definition on every run
instead of maintaining independent numeric copies.

<!-- proxy-architecture-thresholds:start -->
```toml
service_lines = 2600
load_balancer_lines = 3021
http_bridge_mixin_lines = 2436
streaming_mixin_lines = 1100
proxy_service_method_lines = 1200
load_balancer_select_account_lines = 527
```
<!-- proxy-architecture-thresholds:end -->

Implementations SHALL restore or lower these ratchets rather than increase,
bypass, or remove them to make CI pass. A missing, duplicate, malformed,
incomplete, or otherwise invalid threshold definition SHALL fail the
architecture check without preventing unrelated architecture checks from
reporting their own independently evaluable violations.

#### Scenario: OpenSpec-owned ratchets drive the checker

- **WHEN** the normative threshold block changes while the checker implementation remains unchanged
- **THEN** the next architecture-check run enforces the updated OpenSpec-owned values
- **AND** no numeric ratchet must be edited in Python source

#### Scenario: Threshold definition is invalid

- **WHEN** the normative threshold block is missing, duplicated, malformed, incomplete, contains an unknown key, or contains a value that is not a positive integer
- **THEN** the architecture check reports the definition failure and exits non-zero
- **AND** it continues every unrelated architecture check that can still be evaluated

#### Scenario: Multiple ratchets are violated

- **WHEN** more than one independent proxy architecture threshold or boundary is violated
- **THEN** one architecture-check run reports every independently evaluable violation in deterministic order
- **AND** the check exits non-zero

#### Scenario: All architecture gates pass

- **WHEN** the threshold definition is valid and every proxy architecture threshold and boundary is satisfied
- **THEN** the architecture check exits zero
- **AND** it reports that the proxy architecture checks passed

### Requirement: ProxyService remains a stable façade

`app.modules.proxy.service.ProxyService` and the required compatibility exports SHALL remain
available to existing consumers. Behavior extracted from
`ProxyService` or `service.py` SHALL be owned by focused private modules under
`app/modules/proxy/_service/`.
Compatibility shims SHALL remain re-export-only and private service domains
SHALL comply with the repository's explicit cross-domain dependency policy.

#### Scenario: Existing consumers import the proxy façade

- **WHEN** an existing caller imports `ProxyService` or a required compatibility export from `app.modules.proxy.service`
- **THEN** the import resolves to behavior compatible with the pre-change façade
- **AND** no caller migration is required

### Requirement: Account selection orchestration is decomposed without behavior drift

`LoadBalancer.select_account()` SHALL remain the public account-selection entry
point and SHALL delegate cohesive sticky-key retry orchestration and policy to a
private, protocol-typed load-balancer implementation unit. The decomposition
MUST preserve account scope, continuity ownership, security authorization,
exclusions, routing policy, quota and health filtering, concurrency caps,
affinity, stale-state retries, lease cleanup, persistence, result metadata, and
error-code behavior.

#### Scenario: Selection succeeds with or without stickiness

- **WHEN** a request is eligible for account selection with either a sticky key or no sticky key
- **THEN** the selected account, lease, persisted runtime state, and result metadata match the pre-change behavior for the same inputs

#### Scenario: Ownership or capacity prevents selection

- **WHEN** continuity ownership is ambiguous or conflicting, a hard-affinity owner is unavailable, or account caps are exhausted
- **THEN** selection returns the same fail-closed outcome, error code, and mapping-preservation behavior as before the decomposition

#### Scenario: Persistence or cancellation interrupts selection

- **WHEN** persistence fails, a selected row becomes stale, or the selection task is cancelled
- **THEN** acquired leases are released exactly once
- **AND** retries and final errors follow the existing bounded behavior

#### Scenario: Non-sticky selection observes a cache-generation change

- **WHEN** non-sticky selection acquires a lease and the selection-input cache generation changes during persistence
- **THEN** the acquired lease is released exactly once
- **AND** non-sticky selection reloads its inputs and retries within the existing bound

### Requirement: Rust migration preserves explicit ownership boundaries

During incremental migration, Python and Rust MUST NOT both own routing policy
or replay decisions for the same operation. Cross-language boundaries MUST
state which side owns selection, persistence, cancellation, retry eligibility,
and process lifecycle. Shared IPC data MUST live in a versioned protocol crate
without async runtime or networking dependencies, while executable wiring MUST
remain outside reusable transport and domain libraries.

#### Scenario: Native egress remains a transport slice

- **WHEN** Python submits a direct or routed native operation
- **THEN** Python owns account and endpoint selection, health, and replay policy
- **AND** Rust owns only the selected attempt's transport and framed result

#### Scenario: A slice transfers ownership to Rust

- **WHEN** a future migration cutover makes Rust authoritative for a domain
- **THEN** the prior Python owner is removed after contract verification
- **AND** no permanent dual implementation independently makes that domain decision
