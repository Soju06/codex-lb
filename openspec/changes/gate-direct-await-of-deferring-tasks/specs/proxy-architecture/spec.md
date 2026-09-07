## ADDED Requirements

### Requirement: Direct awaits of cancellation-deferring tasks are rejected

The required repository architecture gate SHALL reject a bare `await` of an asyncio task that may defer cancellation. A task MAY defer cancellation when it is created from an async-iterator step (`anext(...)` or `.__anext__()`), from a module function whose body drives an async iterator (`anext`, `__anext__`, or `async for`) or calls a cancellation-deferring helper (including module-level import aliases of the helpers), from the startup-probe task factory, or when it reaches the awaiting function as an `asyncio.Task`-annotated parameter. The gate MUST allow the same task to be awaited through `wait_on_shared_future`, through the cancellation-deferring helpers, or after it has been passed to `asyncio.wait(...)` in the same function, and MUST allow bare awaits of tasks whose coroutine neither drives an async iterator nor defers cancellation. Each violation MUST be reported with its file, line, and a reason distinct from the shield-retry rule.

#### Scenario: Keepalive teardown awaits its chunk task directly

- **WHEN** a function creates a task from an async-iterator step and later awaits that task directly
- **THEN** the gate reports the await's file and line with the direct-await reason
- **AND** the architecture gate exits non-zero

#### Scenario: Task-typed parameter is awaited directly

- **WHEN** a function awaits an `asyncio.Task`-annotated parameter directly
- **THEN** the gate reports the await as a direct-await violation

#### Scenario: Proxy or settled waits remain allowed

- **WHEN** the same task is awaited through `wait_on_shared_future` or a cancellation-deferring helper
- **OR** a bare await follows `asyncio.wait(...)` on that task in the same function
- **THEN** the gate reports no violation

#### Scenario: Plain tasks remain awaitable

- **WHEN** a function creates a task whose coroutine neither drives an async iterator nor calls a deferring helper and awaits it directly
- **THEN** the gate reports no violation
