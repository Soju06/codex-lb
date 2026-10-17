## ADDED Requirements

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
