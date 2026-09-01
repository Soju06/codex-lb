## Why

Exception messages are redacted when copied into ordinary log fields, but the same message is serialized again inside exception tracebacks without the shipped secret-redaction policy. A credential included in an exception can therefore reach operator text and JSON log sinks even though the structured message is safe.

## What Changes

- Apply the existing log-secret patterns to exception traceback text in both shipped application formatter families, one traceback line at a time so no pattern can consume a line terminator.
- Redact a traceback cached on a shared `LogRecord` by another formatter instead of appending it verbatim.
- Treat a recognized JSON-style secret value with no closing quote as extending to the end of its line, and bound Bearer credentials by whitespace or a structural delimiter (`,`, `&`, `;`, quotes, backslash, closing brackets) instead of the token68 alphabet; both shared-pattern changes apply to tracebacks and ordinary fields alike and only add redaction.
- Keep the one-line normalization for ordinary structured error fields unchanged.

## Capabilities

### New Capabilities

- `runtime-log-redaction`: Defines privacy and diagnostic-structure requirements for runtime exception logging.

### Modified Capabilities

None.

## Impact

- `app/core/runtime_logging.py`: `UtcDefaultFormatter` and `JsonFormatter` redact formatted exception text; ordinary field redaction reuses the same per-line policy.
- `tests/unit/test_structured_logging.py`: formatter-level regression coverage for both formatters.
- No settings, migrations, dashboard, or README changes.
