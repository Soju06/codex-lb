## Tasks

- [x] Add regression coverage for retry-safe previous-response misses with a preferred owner account.
- [x] Route retry-safe `previous_response_not_found` through the fresh replay branch before owner-unavailable fail-closed handling.
- [x] Preserve Codex WebSocket `request_kind` metadata in request logs and avoid account-success accounting for empty prewarm completions.
- [x] Bound Codex compact upstream calls by the proxy compact request budget and preserve compact `request_kind` logs.
- [x] Validate focused tests and OpenSpec.
