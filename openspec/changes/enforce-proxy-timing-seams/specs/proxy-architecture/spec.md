## ADDED Requirements

### Requirement: Proxy turn-lifecycle timing seams are enforced

The required repository architecture gate SHALL inspect every Python module under `app/modules/proxy/` and `app/core/utils/shared_future.py` and SHALL reject raw timing, task-spawn and clock sites that bypass the `Scheduler`/`Clock` seams in `app/core/clock.py`. The gate MUST classify each site under exactly one rule: `raw-sleep` (`asyncio.sleep`/`anyio.sleep` with a non-zero delay), `raw-timeout` (`asyncio.wait_for`, a timed `asyncio.wait`, `asyncio.timeout`/`timeout_at`, `anyio.fail_after`/`move_on_after`, and a timed `wait_on_shared_future` without `scheduler=`), `raw-task-spawn` (`asyncio.create_task`, `asyncio.ensure_future`, `asyncio.TaskGroup`, and `create_task` on a loop obtained from `asyncio.get_running_loop()`/`get_event_loop()`), `raw-clock-read` (`time.monotonic()`/`time()`/`perf_counter()`, `loop.time()`/`call_later`/`call_at`, the legacy `_service_time().<x>()` seam and direct `REAL_CLOCK.<x>()` calls, including the `time.time() if now is None else now` default idiom), and `missing-scheduler-kwarg` (a call to a function listed in the configuration that omits a required `scheduler`/`clock`/`scheduler_owner` keyword, matched by the callee's terminal name so facade, attribute and bare calls are covered). A direct `REAL_SCHEDULER.<x>()` call MUST be classified by member (`sleep` as `raw-sleep`, `create_task` as `raw-task-spawn`, otherwise `raw-timeout`). A literal `sleep(0)` yield point, an untimed `asyncio.wait`, `asyncio.shield`, `asyncio.gather`, `loop.create_future()`, `anyio.CancelScope(shield=True)`, `asyncio.to_thread`, `utcnow()` and `datetime.now()` MUST NOT be reported. Alias discovery MUST cover every import in the file, including nested ones, and the canonical `asyncio`/`anyio`/`time` names MUST always be recognised; a function parameter that shadows a recognised name MUST suppress the match; a `**kwargs` splat MUST satisfy a required-keyword rule.

The gate's complete configuration SHALL be defined exactly once in the marked `proxy-timing-seams` TOML block of this specification: `[scheduler_kwarg_required]` maps a function name to its required keyword or list of distinct keywords, and `[allowances.timing]` / `[allowances.clock]` give the accepted raw-site count per repository-relative module for the timing rules (`raw-sleep`, `raw-timeout`, `raw-task-spawn`, `missing-scheduler-kwarg`) and the clock rule respectively. A module not listed SHALL have an allowance of zero. Implementations SHALL restore or lower these allowances rather than increase, bypass, or remove them to make CI pass, and the committed allowances SHALL equal the counts the gate reports so headroom cannot accrue silently. A missing, duplicate, malformed, incomplete, or otherwise invalid definition, or an allowance for a module that is not scanned, SHALL fail the gate by name without preventing module parse failures from being reported. The gate SHALL be able to print the configuration block matching the current tree and to list every site with its rule.

#### Scenario: Raw timing site in an unlisted module

- **WHEN** a module under `app/modules/proxy/` gains a raw `asyncio.sleep`, timed wait, task spawn, clock read or a seam call missing its required keyword and the module has no allowance for that category
- **THEN** the gate reports every site of that category in the module with its rule id, spelling and suggested seam
- **AND** reports the module's count against its allowance
- **AND** the architecture gate exits non-zero

#### Scenario: Yield points and non-timing primitives stay raw

- **WHEN** application code uses a literal `asyncio.sleep(0)`, an untimed `asyncio.wait`, `asyncio.shield`, `asyncio.gather`, `loop.create_future()`, `anyio.CancelScope(shield=True)`, `asyncio.to_thread`, `utcnow()` or `datetime.now()`
- **THEN** the gate does not report the site

#### Scenario: Injected seams pass

- **WHEN** application code schedules through `scheduler_for(owner)` / `self._scheduler`, reads time through `clock_for(owner)` / `self._clock`, or passes `scheduler=` to a timed `wait_on_shared_future`
- **THEN** the gate does not report the site

#### Scenario: Owner-less seam function is called without its collaborator

- **WHEN** a function listed in `[scheduler_kwarg_required]` is called as a bare name, through `self`, or through a facade attribute without one of its required keywords
- **THEN** the gate reports the call under `missing-scheduler-kwarg` naming the missing keyword
- **AND** a call that passes every required keyword or a `**kwargs` splat is not reported

#### Scenario: Aliases and nested imports are resolved, parameter shadows are not

- **WHEN** a raw primitive is reached through a module alias, an imported member alias, a loop bound from `asyncio.get_running_loop()` in the same scope, or an import nested inside a function
- **THEN** the gate reports the site under its rule
- **AND** a function parameter that shadows the recognised name suppresses the report

#### Scenario: Definition is invalid or lists a missing module

- **WHEN** the `proxy-timing-seams` block is missing, duplicated, malformed, has an unknown key or allowance table, a non-positive or non-integer allowance, a malformed keyword list, or lists a module that is not scanned
- **THEN** the gate reports the definition failure by name and exits non-zero
- **AND** module parse failures are still reported

#### Scenario: Allowances are exact and reproducible

- **WHEN** the repository test suite runs
- **THEN** every listed allowance equals the count the gate reports for that module, no unlisted module has a raw site, and the block printed by `--report` parses to the committed configuration

#### Scenario: All timing seam gates pass

- **WHEN** the definition is valid and every module is within its allowances
- **THEN** the gate exits zero and reports that the proxy timing seam checks passed
