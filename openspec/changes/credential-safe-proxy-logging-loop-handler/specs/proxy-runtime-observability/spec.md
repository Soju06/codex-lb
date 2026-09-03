## ADDED Requirements

### Requirement: Loop exception handler output redacts context value reprs

Application startup MUST install an asyncio loop exception handler that
redacts the `repr()` of every context value the default handler would render
(all keys except the textual `message`, `exception`, `source_traceback` and
`handle_traceback` entries) before delegating to the previously installed or
default handler. Secret-free context output MUST be byte-identical to the
default handler output, the exception object and its traceback MUST be passed
through unchanged, installation MUST be idempotent, and any failure inside the
redacting handler MUST fall back to delegating the original context so no
diagnostic is lost.

#### Scenario: Unclosed aiohttp connection repr is credential-free before logging

- **GIVEN** an aiohttp connection is finalized without release and its connection key holds a credentialed proxy URL
- **WHEN** the loop exception handler receives `Unclosed connection` with the connection object in its context
- **THEN** the `asyncio` log record already contains `proxy=URL('scheme://[REDACTED]@host:port')`
- **AND** the password appears nowhere in the record, independent of the formatter in use

#### Scenario: Unretrieved task exception repr is redacted

- **WHEN** a task whose exception text or repr carries URL userinfo or a `Basic <token>` header is garbage-collected unretrieved
- **THEN** the `Task exception was never retrieved` record renders the task repr with `[REDACTED]` in place of the credential

#### Scenario: Secret-free contexts and failures are transparent

- **WHEN** the context contains no secret pattern
- **THEN** the emitted record message and exception info are byte-identical to the default handler output
- **WHEN** a context value's `repr()` raises, or a handler was already installed
- **THEN** the record is still emitted and the previously installed handler still receives the (redacted) context
