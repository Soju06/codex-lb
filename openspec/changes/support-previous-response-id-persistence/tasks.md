## 1. Snapshot persistence

- [x] 1.1 Add a durable response snapshot table and repository for per-response request/response chain state
- [x] 1.2 Wire snapshot repository access into proxy dependencies and service flows
- [x] 1.3 Add migration and regression coverage for snapshot table creation

## 2. `previous_response_id` resolution

- [x] 2.1 Stop rejecting `previous_response_id` by default while continuing to reject `conversation` plus `previous_response_id`
- [x] 2.2 Resolve `previous_response_id` into replayable upstream input history without carrying prior instructions
- [x] 2.3 Return explicit OpenAI-format errors when `previous_response_id` cannot be resolved from persisted snapshots

## 3. Routing continuity

- [x] 3.1 Prefer the stored prior account for resolved `previous_response_id` requests
- [x] 3.2 Fall back to normal account selection when the preferred account is unavailable or ineligible
- [x] 3.3 Add regression coverage for prefer-with-fallback routing

## 4. Stream and websocket parity

- [x] 4.1 Persist snapshots for HTTP streaming and collected `/v1/responses` requests from shared output-item accumulation
- [x] 4.2 Persist snapshots for WebSocket Responses requests and reuse them on follow-up creates
- [x] 4.3 Add integration coverage for HTTP and WebSocket chain continuity across service restart

