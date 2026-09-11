# Company Responses over WebSocket

## Why
Desktop Codex retries company models over WebSocket without taking the assumed HTTP fallback, leaving tasks unable to start.

## What Changes
- Bridge company response.create frames to the existing in-process HTTP Responses route and relay SSE events as WebSocket messages.
- Preserve sequential tool continuation with bounded per-connection response history and exact model ownership.
- Reuse HTTP authentication, source admission, accounting and disconnect cleanup; never route a company bridge request to subscription accounts.

## Impact
Both Responses WebSocket endpoints, company source dispatch telemetry and desktop compatibility.
