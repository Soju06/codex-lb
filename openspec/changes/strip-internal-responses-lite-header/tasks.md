# Tasks

- [x] 1. Add OpenSpec requirement for stripping the internal Responses Lite header before upstream forwarding.
- [x] 2. Strip the Lite header in shared inbound upstream-header filtering and direct upstream builders.
- [x] 3. Add regression coverage for HTTP, compact/shared filtering, internal websocket, and client-facing websocket header builders.
- [x] 4. Validate focused tests and OpenSpec artifacts.
- [x] 5. Preserve Responses Lite `additional_tools` items during instruction normalization.
- [x] 6. Derive canonical HTTP, compact, and per-request websocket Lite signaling from the normalized body.
- [x] 7. Add regression coverage for normalization and HTTP/websocket forwarding, including custom tool-call history.
- [x] 8. Run focused tests, lint, and strict OpenSpec validation.
- [x] 9. Preserve canonical Lite client metadata across HTTP-bridge prefix trimming and retries, with regression coverage.
- [x] 10. Reject untrusted websocket Lite metadata while retaining same-model incremental Lite continuity.
- [x] 11. Establish Lite continuity from accepted prewarms and cover empty and nonempty incremental reuse.
