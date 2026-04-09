## Why
Intermittent HTTP bridge continuity glitches can return `previous_response_not_found` even when the client is still on the same logical conversation. This commonly happens when `x-codex-turn-state` alias continuity is briefly unavailable or request affinity drifts between adjacent turns.

## What Changes
- Add a bridge-local recovery index that maps completed/created `response.id` values to live HTTP bridge sessions.
- Use `previous_response_id` to recover and reuse that mapped live bridge session before fail-closed continuity errors are emitted.
- Keep fail-closed behavior when no live session can be recovered.
- Add regression coverage for recovery when the request key changes between turns.

## Impact
- Reduces intermittent `previous_response_not_found` responses for valid sequential turns.
- Preserves strict fail-closed behavior when continuity is truly gone.
