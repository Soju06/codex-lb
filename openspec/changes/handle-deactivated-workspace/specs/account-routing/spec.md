## ADDED Requirements

### Requirement: Explicit workspace deactivation excludes only the selected routing record

The proxy MUST treat the exact upstream error code `deactivated_workspace` as a terminal unavailable-workspace signal for the selected account routing record. It MUST use the existing permanent-failure status policy to persist `deactivated`, record a workspace-specific reason, and exclude that record from future account selection. It MUST NOT deactivate other account or workspace records solely because they share an identity.

#### Scenario: An unavailable workspace is removed from future selection

- **GIVEN** workspace record A and healthy workspace record B are eligible
- **WHEN** an upstream request through A is rejected with `deactivated_workspace`
- **THEN** A is marked `deactivated` with reason `Workspace has been deactivated`
- **AND** B remains eligible and unchanged
- **AND** subsequent selection does not select A

### Requirement: Movable pre-visible workspace rejections may fail over

When deterministic failover is enabled, the proxy MUST classify `deactivated_workspace` as `account_unavailable` and MAY try the next eligible candidate within the existing attempt budget only before downstream-visible output and only when the request is not bound to an account owner. It MUST NOT retry the rejected workspace or move file-, reasoning-, turn-state-, previous-response-, or single-account-owned payloads to another account. Existing API-key reservation settlement MUST complete before deferred account-health writes.

#### Scenario: An account-neutral Responses request uses a healthy alternative

- **GIVEN** an account-neutral request with no downstream-visible output
- **AND** a healthy alternative candidate remains
- **WHEN** the selected workspace returns HTTP 402 with `deactivated_workspace`
- **THEN** the proxy excludes the rejected record and tries the healthy alternative
- **AND** the caller receives the alternative's completed response rather than the first rejection

#### Scenario: An owner-bound rejection fails closed

- **GIVEN** a request is bound to a file, reasoning, turn-state, previous-response, or single-account owner
- **WHEN** that owner's workspace returns `deactivated_workspace`
- **THEN** the request does not cross accounts or retry the unavailable owner
- **AND** the upstream failure is surfaced through the existing error contract

#### Scenario: Visible output prevents replay

- **GIVEN** downstream-visible output has already been emitted
- **WHEN** the workspace becomes unavailable
- **THEN** no alternative account receives a replay of the request
