## Why

WebSocket `previous_response_not_found` recovery may safely replay a full
Responses resend without the stale anchor, but tool-output-only deltas are not
self-contained. Replaying those deltas as a fresh turn can orphan tool outputs
from their matching tool calls and mutate the conversation incorrectly.

## What Changes

- Classify tool-output-only WebSocket follow-ups as unsafe for fresh retry.
- Preserve fresh retry for full resend payloads that contain enough context.
- Document the retry boundary and cover it with regression tests.

## Impact

- Tool-output deltas fail closed on stale anchors instead of being replayed as
  unrelated fresh turns.
- Full resend recovery behavior remains unchanged.
