## Context

Compact Responses receives an upstream HTTP response whose body can terminate
with an SSE event. Nested OpenAI-style error objects are parsed through the
shared error parser and preserve their type. A top-level event shaped as
`{"type":"error","error_type":...,"code":...,"message":...}` instead uses a
fallback converter.

The status-code helper already reads top-level `error_type`, so it can infer
HTTP 400/401/403/429 correctly. The fallback envelope independently hard-codes
`server_error`, producing an internally inconsistent public response.

## Goals / Non-Goals

**Goals**

- Preserve a supplied non-blank top-level `error_type`.
- Retain `server_error` when the field is absent, non-string, or blank.
- Leave nested envelopes and all other mapped fields/statuses unchanged.

**Non-Goals**

- Change compact request routing, retries, account selection, or health.
- Infer new status codes or normalize arbitrary upstream error types.
- Change non-compact Responses or nested error parsing.

## Decisions

### Fix only the top-level fallback

The fallback converter reads `payload["error_type"]`. A string containing at
least one non-whitespace character becomes the OpenAI detail `type`; otherwise
the existing `server_error` value remains.

This keeps the fix at the data-loss seam and avoids changing the shared parser
or status inference that already behave correctly.

### Preserve supplied type text

Whitespace is used only to decide whether a value is blank. A non-blank string
is forwarded verbatim, matching the existing field-preservation behavior for
top-level `code`, `message`, and `param`.

## Risks / Trade-offs

- Upstream can supply an unfamiliar type. Preserving it is preferable to
  fabricating `server_error` and matches OpenAI-compatible passthrough behavior.
- The fallback remains intentionally conservative for absent, non-string, or
  whitespace-only values.

## Migration Plan

No migration, setting, or rollout step is required. Rollback restores the
previous top-level type substitution.

## Open Questions

None.
