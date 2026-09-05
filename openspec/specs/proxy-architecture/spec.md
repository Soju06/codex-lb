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

The proxy turn-lifecycle timing seams are enforced by
`scripts/check_proxy_timing_seams.py`, which loads its required-keyword list
and per-module raw-site allowances from the marked TOML block below. Unlisted
modules have an allowance of zero; a raw site is exempted only by editing this
block in the same change that introduces it, and the committed allowances SHALL
equal the counts the checker reports (restore or lower rather than increase).

<!-- proxy-timing-seams:start -->
```toml
[scheduler_kwarg_required]
_await_cancelled_task = "scheduler"
_await_cleanup_deferring_cancellation = "scheduler"
_await_owned_websocket_task_after_reader_cancellation = "scheduler"
_await_result_deferring_cancellation = "scheduler"
_cancel_and_track_cancelled_task = "scheduler"
_cancel_http_bridge_reader_child = "scheduler_owner"
_create_first_stream_probe_task = "scheduler"
_iter_account_capacity_recovery_wait = ["scheduler", "clock"]
_iter_account_capacity_wait_sse = ["scheduler", "clock"]
_iter_sse_event_blocks = ["scheduler", "clock"]
_probe_chat_stream_startup_error = ["scheduler", "clock"]
_probe_stream_startup_error = ["scheduler", "clock"]
_process_parsed_http_bridge_upstream_event = ["scheduler", "clock"]
_release_reservation_best_effort = "scheduler"
_release_websocket_response_create_gate = "scheduler"
_sleep_for_account_selection_recovery = ["scheduler", "clock"]
_stream_proxy_errors_as_response_failed = "scheduler"
_stream_response_error_events = "scheduler"
_wait_before_http_bridge_model_capacity_retry = ["scheduler", "clock"]
_wait_for_first_stream_probe = ["scheduler", "clock"]
_wait_for_websocket_continuity_gap = ["scheduler", "clock"]

[allowances.timing]  # raw-sleep + raw-timeout + raw-task-spawn + missing-scheduler-kwarg; unlisted modules = 0
"app/core/utils/shared_future.py" = 1
"app/modules/proxy/_service/compact.py" = 3
"app/modules/proxy/_service/http_bridge/mixin.py" = 1
"app/modules/proxy/_service/realtime_live.py" = 5
"app/modules/proxy/_service/request_log.py" = 1
"app/modules/proxy/api.py" = 19
"app/modules/proxy/http_bridge_event_batcher.py" = 2

[allowances.clock]  # raw-clock-read; unlisted modules = 0
"app/modules/proxy/_service/clock_budget.py" = 1
"app/modules/proxy/_service/codex_control.py" = 2
"app/modules/proxy/_service/compact.py" = 6
"app/modules/proxy/_service/file_ops.py" = 2
"app/modules/proxy/_service/http_bridge/helpers.py" = 1
"app/modules/proxy/_service/rate_limit.py" = 2
"app/modules/proxy/_service/realtime_live.py" = 2
"app/modules/proxy/_service/request_log.py" = 2
"app/modules/proxy/_service/support.py" = 2
"app/modules/proxy/_service/transcribe.py" = 2
"app/modules/proxy/_service/warmup.py" = 2
"app/modules/proxy/_service/websocket/helpers.py" = 3
"app/modules/proxy/account_cache.py" = 2
"app/modules/proxy/account_eligibility.py" = 1
"app/modules/proxy/api.py" = 8
"app/modules/proxy/durable_bridge_repository.py" = 2
"app/modules/proxy/images_observability.py" = 1
"app/modules/proxy/images_service.py" = 2
"app/modules/proxy/load_balancer.py" = 3
"app/modules/proxy/rate_limit_cache.py" = 2
```
<!-- proxy-timing-seams:end -->

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
