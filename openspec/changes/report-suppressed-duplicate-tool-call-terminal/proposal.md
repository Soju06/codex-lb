# Report Suppressed Duplicate Tool-Call Terminals

## Why

A replayed side-effecting tool call can be suppressed intentionally, but the
terminal currently looks like an incomplete upstream stream. That false label
also feeds the HTTP bridge retry circuit.

## What Changes

- Emit `duplicate_tool_call_replay_suppressed` for the suppressed duplicate
  terminal instead of `stream_incomplete`.
- Keep the request non-successful while avoiding account-health penalties.
- Keep the dedicated duplicate terminal out of retry-circuit failure classes.
