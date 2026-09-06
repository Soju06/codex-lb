## Why

Rendered WARNING+ log records already redact keyed secrets, including exception
and stack text, but the shared secret patterns run on the entire rendered
string. An `authorization=` value can therefore consume following traceback
lines, an unterminated JSON secret at end of line is left intact, and a Bearer
token stops at `:` so a glued credential tail leaks.

## What Changes

- Apply the keyed/bearer/authorization/JSON/Python-repr secret patterns one
  CR/LF-delimited line at a time.
- Redact unterminated JSON secret values through the end of the current line.
- Treat a same-line `:` tail after a Bearer token as credential material
  (isolated from the keyed `secret=` pattern).
- Leave the `_RedactingFormatterMixin` path, the WARNING+ keyed-secret gate,
  and comma/`&` same-line truncation unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Require line-bounded secret-pattern
  application, unterminated JSON redaction through end of line, and Bearer
  colon-tail consumption. This is a delta on the existing rendered-record
  redaction policy from `credential-safe-proxy-logging`, not a new capability.

## Impact

- Code: `app/core/runtime_logging.py`.
- Tests: `tests/unit/test_structured_logging.py`.
- No settings, dependencies, schemas, routes, database, or frontend changes.
- Out of scope (issue #2028): Digest/AWS auth-param lists, quoted Python header
  keys, and whitespace-separated Bearer tails.
