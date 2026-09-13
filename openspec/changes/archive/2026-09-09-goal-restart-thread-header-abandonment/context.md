## Purpose

Close the `#1703` × `#1680` composition hole: current Codex always
sends `thread-id`, so the merged goal-restart recovery never fires.

## Decision

Abandonment stays a `session_header` *interpretation* of the raw
process-session key. Request locality may be `thread_header`. Explicit
`turn_state` is unchanged.

## Failure modes

- Incremental or file-pinned restarts must still fail closed on the
  required owner.
- After retirement, a later thread-id turn must not revive the raw
  row as hard ownership.

## Example

Process session `sid` maps to quota-exceeded account A. Codex resends
an account-neutral goal body with `session-id: sid` and
`thread-id: t1`. Selection retires `sid` for `session_header`, routes
to B, and later `t1` turns stay on B.
