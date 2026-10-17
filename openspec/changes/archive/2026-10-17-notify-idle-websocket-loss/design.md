## Context

Completed Codex turns can leave a connection-local context anchor for later deltas. Existing terminal handling has no request to fail when the upstream disappears while idle, so the downstream can wrongly remain reusable.

## Decisions

Only when a completed response was delivered and no pending or replay-owned request exists, send downstream close 1012 and mark it disconnected. Retire the shared continuity anchor only if its connection identity matches the lost upstream, preserving a newer anchor established elsewhere. Leave durable account ownership untouched. Keep existing terminal handling for active work and OpenAI-compatible WebSockets.

## Risks and boundaries

Physical loss requires the client to reconnect with sufficient context; the proxy must not fabricate it. A concurrent unsent request retains its existing sender ownership and is not treated as idle. The connection identity is internal and has no wire, settings or database representation.
