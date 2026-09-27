## Why

Codex v2 creates a local `agent_message` when delegating a task, including an inline encrypted message. Source pooling mistakes its local ID for an unknown upstream reference and returns 409 before contacting an endpoint that can read the message.

## What Changes

- Classify strictly validated inline agent messages as portable direct-source input.
- Keep their original IDs, authors, recipients, content and ordering on the wire.
- Preserve ownership checks on accompanying responses, reasoning, compaction, calls and malformed input.
- Add route and cross-replica regression coverage for initial tasks and continuations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: inline Codex agent-message portability.

## Impact

Direct HTTP Responses classification and ownership extraction only. No database migration, settings, credentials, subscription replay changes or runtime state mutation.
