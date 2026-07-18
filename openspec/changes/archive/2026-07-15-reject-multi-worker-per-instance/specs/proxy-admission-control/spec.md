# proxy-admission-control

## ADDED Requirements

### Requirement: Multiple worker processes per instance are rejected for shared per-account caps

Per-account concurrency caps are partitioned per bridge-ring replica and are correct only when a single worker process runs behind each bridge-ring instance id. The system MUST expose `workers_per_instance` (env `CODEX_LB_WORKERS_PER_INSTANCE`, default 1, minimum 1) as an explicit operator declaration of how many worker processes an instance runs behind one instance id. When `workers_per_instance` is greater than 1 the process MUST fail fast at startup with a settings validation error that names `CODEX_LB_WORKERS_PER_INSTANCE` and states that running more than one worker per instance is not supported for shared per-account caps and that operators MUST run one worker per pod/container and scale horizontally via replicas. When `workers_per_instance` is 1 (the default) startup MUST proceed with no operator action required and behavior MUST be identical to a deployment that does not set the variable. The system MUST NOT attempt to auto-detect the worker count and MUST NOT partition per-account caps across intra-pod worker processes.

#### Scenario: A single worker per instance is accepted

- **GIVEN** `workers_per_instance` is 1 (the default, whether unset or explicitly set)
- **WHEN** the process loads its settings at startup
- **THEN** startup succeeds and per-account caps remain partitioned per replica via the bridge ring

#### Scenario: More than one worker per instance fails fast

- **GIVEN** `workers_per_instance` is configured as 2
- **WHEN** the process loads its settings at startup
- **THEN** startup fails with a settings validation error naming `CODEX_LB_WORKERS_PER_INSTANCE`
- **AND** the error states multi-worker-per-instance is not supported and directs the operator to run one worker per pod/container and scale via replicas

## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST enforce account-local response-create and streaming concurrency limits in addition to process-wide admission limits, and the configured limits MUST be cluster-wide per-account targets enforced across all replicas rather than per-replica allowances. Because per-account caps are partitioned per replica via the bridge ring and cannot be safely partitioned across intra-pod worker processes, each instance MUST run a single worker process; horizontal scaling is achieved by adding replicas. The default account response-create cap MUST be 4 and the default account stream cap MUST be 8 unless operators configure a different value. When an account is at either cap, new soft-affinity work MUST prefer another eligible account before returning local overload. Hard-continuity work MAY fail closed when the required owner account is saturated.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a soft-affinity `/v1/responses` request is routed
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality
