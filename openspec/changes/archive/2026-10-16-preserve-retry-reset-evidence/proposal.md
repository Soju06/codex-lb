## Why

HTTP bridge and direct WebSocket retry branches discard upstream quota-reset
metadata before recording account health. A multi-day usage limit becomes a
short fallback cooldown, so existing owner retirement can keep an exhausted
account bound to a conversation while healthy replacements are available.

## What Changes

- Preserve parsed absolute and relative reset metadata in retry health writes,
  including deferred writes after API-key reservation settlement.
- Reuse the existing error parser and reject JSON booleans as numeric metadata.
- Restore and verify the focused fix and regressions from closed, unmerged
  PR #2440, with attribution and without its unrelated dependency changes.
- Keep existing ownership, replay, file-pin, and settlement boundaries intact.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: retry health retains the upstream reset evidence
  needed by existing account selection and unavailable-owner retirement.

## Impact

Shared OpenAI error parsing, HTTP bridge retry health, and direct WebSocket
retry health; route-level and settlement regressions; OpenSpec requirements
and context. No schema, settings, dependency, or public envelope changes.

Partial fix for #1707, following #2440. Transient hard-owner backoff (#2486)
and Codex automatic-reviewer continuation recovery remain separate concerns.
