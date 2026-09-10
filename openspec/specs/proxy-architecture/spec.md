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
and per-module raw-site allowances from the marked TOML block below. Timing
allowances are tracked per rule (`{ raw-sleep = 1, raw-timeout = 2 }`), so a
module cannot offset a new raw site of one kind against a removed site of
another; the single-rule clock table is written as plain integers. Unlisted
modules and rules have an allowance of zero; a raw site is exempted only by
editing this block in the same change that introduces it, and the committed
allowances SHALL equal the counts the checker reports (restore or lower rather
than increase).

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

[allowances.timing]  # per-rule counts of raw-sleep + raw-timeout + raw-task-spawn + missing-scheduler-kwarg; unlisted modules and rules = 0
"app/core/utils/shared_future.py" = { raw-task-spawn = 1 }
"app/modules/proxy/_service/compact.py" = { raw-sleep = 1, raw-timeout = 1, raw-task-spawn = 1 }
"app/modules/proxy/_service/http_bridge/mixin.py" = { raw-timeout = 1 }
"app/modules/proxy/_service/realtime_live.py" = { raw-timeout = 2, raw-task-spawn = 3 }
"app/modules/proxy/_service/request_log.py" = { raw-timeout = 1 }
"app/modules/proxy/api.py" = { missing-scheduler-kwarg = 19 }
"app/modules/proxy/http_bridge_event_batcher.py" = { raw-timeout = 1, raw-task-spawn = 1 }

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
Private service domains SHALL comply with the repository's explicit cross-domain
dependency policy. The proxy package SHALL NOT carry re-export-only
compatibility shim modules that have no importers.

#### Scenario: Existing consumers import the proxy façade

- **WHEN** an existing caller imports `ProxyService` or a required compatibility export from `app.modules.proxy.service`
- **THEN** the import resolves to behavior compatible with the pre-change façade
- **AND** no caller migration is required

#### Scenario: Importer-less compatibility shim is removed

- **WHEN** a private re-export-only module under `app/modules/proxy/` has no importers in `app/`, `tests/`, or `scripts/`
- **THEN** the module is deleted rather than retained
- **AND** the architecture check has no rule that requires the deleted module to exist

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

### Requirement: Cancellation retry safety gate is enforced

The required repository architecture gate SHALL inspect application Python code and SHALL reject any loop that catches caller cancellation and retries an `asyncio.shield()` wait. The gate MUST detect direct, imported, and aliased shield calls, including a shield assigned before the guarded await; MUST treat bare handlers and `BaseException` handlers as cancellation-catching; and MUST reject the retry even when the loop is nested inside `anyio.CancelScope(shield=True)`.

The gate MUST inspect ordinary and exception-group handlers and MUST evaluate `if`, `match`, and `try`/`finally` paths so a nested retry cannot bypass detection and a handler whose every path raises or returns is not treated as retrying. A `break` MUST be classified conservatively as a retry because an enclosing loop can re-enter the shield wait. Shield discovery MUST remain within the inspected lexical body and MUST NOT descend into a merely defined nested function or class. Alias discovery MUST be limited to module-level imports so an unrelated nested-scope import cannot redefine aliases for the whole file, and a function parameter that shadows a recognized alias or exception spelling MUST NOT create a false violation. A shield wait that propagates cancellation without retrying and a cancellation-deferring loop that uses the canonical shared-future wait MUST remain allowed.

#### Scenario: Cancellation handler retries a shield wait

- **WHEN** application code catches cancellation in a loop and any handler path retries an `asyncio.shield()` wait
- **THEN** the cancellation safety gate reports the shield call's file and line
- **AND** the architecture gate exits non-zero

#### Scenario: AnyIO shielding does not exempt repeated asyncio shielding

- **WHEN** a cancellation-catching shield retry loop is nested inside `anyio.CancelScope(shield=True)`
- **THEN** the cancellation safety gate still rejects the loop
- **AND** the implementation is directed to the canonical shared-future wait

#### Scenario: Every cancellation path terminates

- **WHEN** a loop awaits `asyncio.shield()` but every `if`, `match`, or `try`/`finally` path through its cancellation handler raises or returns
- **THEN** the cancellation safety gate does not report a retry violation

#### Scenario: Break is conservatively rejected

- **WHEN** a cancellation handler breaks its immediate loop
- **THEN** the cancellation safety gate reports the retry violation because an enclosing loop can execute the shield wait again

#### Scenario: Shield exists only inside a nested definition

- **WHEN** a retrying handler's guarded body defines but does not invoke a nested function containing `asyncio.shield()`
- **AND** the guarded body itself does not execute a shield wait
- **THEN** the cancellation safety gate does not report the nested definition as a retry violation

#### Scenario: Nested import or parameter shadow does not leak an alias

- **WHEN** an unrelated nested scope imports an asyncio shield alias
- **OR** a function parameter shadows a recognized module, shield, or cancellation-exception spelling
- **THEN** the nested import or parameter does not classify the function's call and handler as an asyncio cancellation retry

### Requirement: Proxy turn-lifecycle timing seams are enforced

The required repository architecture gate SHALL inspect every Python module under `app/modules/proxy/` and `app/core/utils/shared_future.py` and SHALL reject raw timing, task-spawn and clock sites that bypass the `Scheduler`/`Clock` seams in `app/core/clock.py`. The gate MUST classify each site under exactly one rule: `raw-sleep` (`asyncio.sleep`/`anyio.sleep` with a non-zero delay, `anyio.sleep_forever`/`sleep_until`, and the blocking `time.sleep`), `raw-timeout` (`asyncio.wait_for`, a timed `asyncio.wait`, `asyncio.timeout`/`timeout_at`, `anyio.fail_after`/`move_on_after`/`fail_at`/`move_on_at`, and a timed `wait_on_shared_future` without `scheduler=`), `raw-task-spawn` (`asyncio.create_task`, `asyncio.ensure_future`, `asyncio.TaskGroup`, `anyio.create_task_group`, and `create_task` on a loop obtained from `asyncio.get_running_loop()`/`get_event_loop()`), `raw-clock-read` (`time.monotonic()`/`time()`/`perf_counter()`, `loop.time()`/`call_later`/`call_at`, the legacy `_service_time().<x>()` seam and direct `REAL_CLOCK.<x>()` calls, including the `time.time() if now is None else now` default idiom), and `missing-scheduler-kwarg` (a call to a function listed in the configuration that omits a required `scheduler`/`clock`/`scheduler_owner` keyword, matched by the callee's terminal name so facade, attribute and bare calls are covered). A direct `REAL_SCHEDULER.<x>()` call MUST be classified by member (`sleep` as `raw-sleep`, `create_task` as `raw-task-spawn`, otherwise `raw-timeout`). A literal `asyncio.sleep(0)` yield point, an untimed `asyncio.wait`, `asyncio.shield`, `asyncio.gather`, `loop.create_future()`, `anyio.CancelScope(shield=True)`, `asyncio.to_thread`, `utcnow()` and `datetime.now()` MUST NOT be reported. Alias discovery MUST cover every import in the file, including nested ones, and the canonical `asyncio`/`anyio`/`time` names MUST always be recognised; a function parameter that shadows a recognised name MUST suppress the match; a `**kwargs` splat MUST satisfy a required-keyword rule.

The gate's complete configuration SHALL be defined exactly once in the marked `proxy-timing-seams` TOML block of the stable specification file `openspec/specs/proxy-architecture/spec.md`, which is the file the checker reads: `[scheduler_kwarg_required]` maps a function name to its required keyword or list of distinct keywords, and `[allowances.timing]` / `[allowances.clock]` give the accepted raw-site counts per repository-relative module for the timing rules (`raw-sleep`, `raw-timeout`, `raw-task-spawn`, `missing-scheduler-kwarg`) and the clock rule respectively. A timing allowance SHALL be tracked per rule as an inline table (`{ raw-sleep = 1, raw-timeout = 2 }`) so that removing a raw site of one rule cannot offset adding a raw site of another; a plain integer MAY be accepted as a category total for compatibility, but `--report` SHALL emit per-rule counts and the committed block SHALL use them. The single-rule clock table is written as plain integers. A module or rule not listed SHALL have an allowance of zero. Implementations SHALL restore or lower these allowances rather than increase, bypass, or remove them to make CI pass, and the committed allowances SHALL equal the counts the gate reports so headroom cannot accrue silently. A missing, duplicate, malformed, incomplete, or otherwise invalid definition, a per-rule table naming a rule outside its category, or an allowance for a module that is not scanned, SHALL fail the gate by name without preventing module parse failures from being reported. The gate SHALL be able to print the configuration block matching the current tree and to list every site with its rule.

#### Scenario: Raw timing site in an unlisted module

- **WHEN** a module under `app/modules/proxy/` gains a raw `asyncio.sleep`, timed wait, task spawn, clock read or a seam call missing its required keyword and the module has no allowance for that rule
- **THEN** the gate reports every site of that rule in the module with its rule id, spelling and suggested seam
- **AND** reports the module's count for that rule against its allowance
- **AND** the architecture gate exits non-zero

#### Scenario: Yield points and non-timing primitives stay raw

- **WHEN** application code uses a literal `asyncio.sleep(0)`, an untimed `asyncio.wait`, `asyncio.shield`, `asyncio.gather`, `loop.create_future()`, `anyio.CancelScope(shield=True)`, `asyncio.to_thread`, `utcnow()` or `datetime.now()`
- **THEN** the gate does not report the site

#### Scenario: Trading one timing rule for another is rejected

- **WHEN** a module's per-rule timing allowance accepts one `missing-scheduler-kwarg` site and the module instead contains one raw `asyncio.sleep` and no missing-keyword site, so its timing total is unchanged
- **THEN** the gate reports the raw sleep under `raw-sleep` against an allowance of zero and exits non-zero

#### Scenario: Injected seams pass

- **WHEN** application code schedules through `scheduler_for(owner)` / `self._scheduler`, reads time through `clock_for(owner)` / `self._clock`, or passes `scheduler=` to a timed `wait_on_shared_future`
- **THEN** the gate does not report the site

#### Scenario: Owner-less seam function is called without its collaborator

- **WHEN** a function listed in `[scheduler_kwarg_required]` is called as a bare name, through `self`, or through a facade attribute without one of its required keywords
- **THEN** the gate reports the call under `missing-scheduler-kwarg` naming the missing keyword
- **AND** a call that passes every required keyword or a `**kwargs` splat is not reported

#### Scenario: Aliases and nested imports are resolved, parameter shadows are not

- **WHEN** a raw primitive (including `time.sleep`, `anyio.sleep_forever`/`sleep_until`, `anyio.fail_at`/`move_on_at` or `anyio.create_task_group`) is reached through a module alias, an imported member alias, a loop bound from `asyncio.get_running_loop()` in the same scope, or an import nested inside a function
- **THEN** the gate reports the site under its rule
- **AND** a function parameter that shadows the recognised name suppresses the report

#### Scenario: Definition is invalid or lists a missing module

- **WHEN** the `proxy-timing-seams` block is missing, duplicated, malformed, has an unknown key or allowance table, a non-positive or non-integer allowance, an empty per-rule table, a per-rule table naming an unknown rule or a non-positive count, a malformed keyword list, or lists a module that is not scanned
- **THEN** the gate reports the definition failure by name and exits non-zero
- **AND** module parse failures are still reported

#### Scenario: Allowances are exact and reproducible

- **WHEN** the repository test suite runs
- **THEN** every listed timing allowance is a per-rule table equal to the per-rule counts the gate reports for that module, every listed clock allowance equals its count, no unlisted module has a raw site, and the block printed by `--report` parses to the committed configuration

#### Scenario: All timing seam gates pass

- **WHEN** the definition is valid and every module is within its allowances
- **THEN** the gate exits zero and reports that the proxy timing seam checks passed

### Requirement: AnyIO synchronization primitives survive cancelled-waiter releases

The proxy uses two families of asyncio synchronization primitives: the AnyIO asyncio-backend `anyio.Lock` and `anyio.Semaphore` (every HTTP-bridge session `pending_lock`, the bridge registry lock, websocket mixin locks, and cache locks) and the stdlib `asyncio.Lock` and `asyncio.Semaphore`. When a release happens while a queued waiter has already been cancelled but has not yet removed itself from the waiter queue, each primitive MUST satisfy the following:

- **Lock**: the release MUST either hand ownership to a live (non-cancelled) queued waiter or leave the lock unowned such that the next `acquire()` call succeeds. A release MUST NOT leave the lock with no owner and a queued waiter that is never woken.
- **Semaphore**: the released permit MUST become available to a live queued waiter or to the next acquirer. A release MUST NOT strand the permit behind a cancelled waiter entry so that waiter progress stops while the permit count is positive.

The stdlib primitives already satisfy this (they skip cancelled waiters on acquire) and serve as the reference behaviour. The repository MUST declare an AnyIO dependency floor whose asyncio-backend implementation satisfies this (anyio 4.14.0 or later) and MUST keep a deterministic regression that drives the release/cancel/acquire interleaving against `anyio.Lock`, `anyio.Semaphore`, and the stdlib reference.

#### Scenario: AnyIO Lock newcomer acquires after a release coinciding with a cancelled waiter

- **GIVEN** task O holds an `anyio.Lock` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the lock within the same bounded wait
- **AND** the lock reports no owner and no waiting tasks after A releases

#### Scenario: AnyIO Semaphore newcomer acquires a permit after a release coinciding with a cancelled waiter

- **GIVEN** task O holds the only permit of an `anyio.Semaphore(1)` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the permit within the same bounded wait
- **AND** the semaphore reports its full permit count and no waiting tasks after A releases

#### Scenario: Stdlib reference behaviour is preserved

- **GIVEN** the same interleaving is driven against the stdlib `asyncio.Lock`
- **THEN** the newcomer acquires within the same bounded wait

#### Scenario: Dependency floor pins the fixed primitive

- **WHEN** the project dependencies are resolved
- **THEN** the resolved anyio version is at least 4.14.0
- **AND** the regression covering the cancelled-waiter release interleaving passes for both AnyIO primitives and the stdlib reference

### Requirement: Direct awaits of cancellation-deferring tasks are rejected

The required repository architecture gate SHALL reject a bare `await` of an asyncio task that may defer cancellation. A task MAY defer cancellation when it is created from an async-iterator step (`anext(...)` or `.__anext__()`), from a coroutine function, resolved lexically at the creation site (a nested definition or a module-level one, whichever the innermost enclosing scope binds), whose body drives an async iterator (`anext`, `__anext__`, or `async for`) or calls a cancellation-deferring helper (including module-level import aliases of the helpers), from the startup-probe task factory, or when it reaches the awaiting function as an `asyncio.Task`-annotated parameter. Coroutine calls MUST be resolved lexically: the innermost enclosing scope that defines the name wins, so a local definition shadows a module-level one of the same name and a nested deferring coroutine is detected where it is created. The gate MUST allow the same task to be awaited through `wait_on_shared_future` or the cancellation-deferring helpers, and MUST allow a bare await only where a preceding `asyncio.wait(...)` proves that task settled: inside `if task in done:`, inside `for task in done:`, or after `asyncio.wait({task})` followed by an `if not done:` guard that raises or returns. A `timeout` or `return_when` argument, a non-`asyncio` `.wait(...)`, or an await placed before the wait MUST NOT count as proof. Bare awaits of tasks whose coroutine neither drives an async iterator nor defers cancellation remain allowed. Each violation MUST be reported with its file, line, and a reason distinct from the shield-retry rule.

#### Scenario: Keepalive teardown awaits its chunk task directly

- **WHEN** a function creates a task from an async-iterator step and later awaits that task directly
- **THEN** the gate reports the await's file and line with the direct-await reason
- **AND** the architecture gate exits non-zero

#### Scenario: Nested deferring coroutine is detected

- **WHEN** a function defines a nested coroutine whose body drives an async iterator (for example a `_next_chunk` reading `it.__anext__()`), creates a task from it, and awaits that task directly
- **THEN** the gate reports the await as a direct-await violation

#### Scenario: Task-typed parameter is awaited directly

- **WHEN** a function awaits an `asyncio.Task`-annotated parameter directly
- **THEN** the gate reports the await as a direct-await violation

#### Scenario: Proxy or proven-settled waits remain allowed

- **WHEN** the same task is awaited through `wait_on_shared_future` or a cancellation-deferring helper
- **OR** a bare await is guarded by `if task in done:` or `for task in done:` after a preceding `asyncio.wait(...)`
- **THEN** the gate reports no violation

#### Scenario: Unproven settlement is still a violation

- **WHEN** a bare await of the task follows `asyncio.wait(...)` with a `timeout` or `return_when` but no membership guard
- **OR** follows a non-`asyncio` `.wait(...)`
- **OR** precedes the `asyncio.wait(...)`
- **THEN** the gate reports the direct-await violation

#### Scenario: Local definitions shadow module-level ones

- **WHEN** a function creates a task from a local coroutine function that neither iterates nor defers while a module-level function of the same name does
- **THEN** the gate reports no violation

#### Scenario: Plain tasks remain awaitable

- **WHEN** a function creates a task whose coroutine neither drives an async iterator nor calls a deferring helper and awaits it directly
- **THEN** the gate reports no violation

