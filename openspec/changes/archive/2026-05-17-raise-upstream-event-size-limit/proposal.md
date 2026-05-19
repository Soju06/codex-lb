# Proposal: raise-upstream-event-size-limit

## Why

Recent Codex Desktop builds can request built-in tools such as `image_generation`, which may produce large upstream Responses events. The proxy currently caps upstream SSE events and websocket message frames at 2 MiB, which is too low for legitimate image payloads and causes local websocket `1009 message too big` disconnects before `response.completed`.

## What Changes

- Raise the default upstream Responses event/message size limit from 2 MiB to 16 MiB.
- Keep the existing configuration knob (`max_sse_event_bytes`) so operators can still override the limit.

## Impact

- Prevents local `1009` disconnects for large but valid Responses tool outputs.
- Aligns the default limit with the common 16 MiB websocket ceiling already assumed by the proxy's `response.create` budget logic.
