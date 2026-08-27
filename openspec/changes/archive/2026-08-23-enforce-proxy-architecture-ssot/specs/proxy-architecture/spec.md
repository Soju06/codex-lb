## MODIFIED Requirements

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
