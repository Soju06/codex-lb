# Change: fix SSE CR/LF line boundary parsing

## Why

Streaming Responses traffic can contain unescaped Unicode line-separator
characters inside JSON string values. Python's `str.splitlines()` treats those
characters as line breaks, which can corrupt an otherwise valid `data:` payload
before JSON decoding.

## What Changes

- Define the Responses SSE parser contract so only CR, LF, and CRLF delimit SSE
  lines.
- Treat CR-only blank lines as complete HTTP streaming SSE event separators.
- Preserve Unicode separator characters such as U+2028 and U+2029 when they
  appear inside a `data:` payload.
- Keep existing multi-line `data:` joining semantics for CR/LF/CRLF-delimited
  blocks.
- Preserve the original SSE event terminator style when normalizing legacy
  upstream event aliases.

## Impact

Clients streaming Responses events with Unicode line separators inside JSON
strings keep receiving valid events instead of dropped or malformed parser
results.
