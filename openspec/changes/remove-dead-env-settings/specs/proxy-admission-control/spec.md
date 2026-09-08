## MODIFIED Requirements

### Requirement: Multiple worker processes per instance are rejected for shared per-account caps

Per-account concurrency caps are partitioned per bridge-ring replica and are correct only when a single worker process runs behind each bridge-ring instance id. `CODEX_LB_WORKERS_PER_INSTANCE` MUST be treated as a startup guard on the environment rather than a configurable setting: the only supported value is `1`, so it MUST NOT be a `Settings` field or appear in the settings reference as a tunable. When the environment (process environment or the loaded env files) declares `CODEX_LB_WORKERS_PER_INSTANCE` — matched case-insensitively, as the former `Settings` field was — with any value other than `1` — a larger integer, zero, a negative number, or a non-integer — the process MUST fail fast at startup with a settings validation error that names `CODEX_LB_WORKERS_PER_INSTANCE`; for values greater than 1 the error MUST state that running more than one worker per instance is not supported for shared per-account caps and that operators MUST run one worker per pod/container and scale horizontally via replicas. When the variable is unset or `1`, startup MUST proceed with no operator action required and behavior MUST be identical to a deployment that does not set the variable. The system MUST NOT attempt to auto-detect the worker count and MUST NOT partition per-account caps across intra-pod worker processes.

#### Scenario: A single worker per instance is accepted

- **GIVEN** `CODEX_LB_WORKERS_PER_INSTANCE` is unset or explicitly `1`
- **WHEN** the process loads its settings at startup
- **THEN** startup succeeds and per-account caps remain partitioned per replica via the bridge ring

#### Scenario: More than one worker per instance fails fast

- **GIVEN** `CODEX_LB_WORKERS_PER_INSTANCE=2`
- **WHEN** the process loads its settings at startup
- **THEN** startup fails with a settings validation error naming `CODEX_LB_WORKERS_PER_INSTANCE`
- **AND** the error states multi-worker-per-instance is not supported and directs the operator to run one worker per pod/container and scale via replicas

#### Scenario: A lowercase declaration is not a bypass

- **GIVEN** `codex_lb_workers_per_instance=2` declared in lowercase
- **WHEN** the process loads its settings at startup
- **THEN** startup fails with the same settings validation error naming `CODEX_LB_WORKERS_PER_INSTANCE`

#### Scenario: A malformed declaration fails fast

- **GIVEN** `CODEX_LB_WORKERS_PER_INSTANCE=0` or `CODEX_LB_WORKERS_PER_INSTANCE=two`
- **WHEN** the process loads its settings at startup
- **THEN** startup fails with a settings validation error naming `CODEX_LB_WORKERS_PER_INSTANCE` and stating that only `1` is supported
