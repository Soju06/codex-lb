## Context

Completed Codex turns can leave a connection-local context anchor for later deltas. Existing terminal handling has no request to fail when the upstream disappears while idle, so the downstream can wrongly remain reusable.

## Decisions

Only when a completed response was delivered and no pending or replay-owned request exists, send downstream close 1012 and mark it disconnected. Retire the shared continuity anchor only if its connection identity matches the lost upstream, preserving a newer anchor established elsewhere. Leave durable account ownership untouched. Keep existing terminal handling for active work and OpenAI-compatible WebSockets.

## Risks and boundaries

Physical loss requires the client to reconnect with sufficient context; the proxy must not fabricate it. A registered unsent request retains its existing sender ownership and prevents an idle close. A received turn can still be awaiting asynchronous preparation or admission before registration. If the idle close wins that race, registration checks the disconnected state under the same pending lock, keeps the request in scope-cleanup ownership and exits before dispatch. The connection identity is internal and has no wire, settings or database representation.
