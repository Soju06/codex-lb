## ADDED Requirements

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
