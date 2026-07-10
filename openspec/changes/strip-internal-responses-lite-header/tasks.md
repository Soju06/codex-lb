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
- [x] 12. Require incremental Lite trust to reference the accepted Lite response via `previous_response_id`, keep non-Lite acceptances from clobbering recorded Lite continuity, and cover trusted and untrusted linkage paths.
- [x] 13. Strip the trusted marker from transparent fresh full-resend replays that clear `previous_response_id` without an `additional_tools` prefix, with unit and websocket-route regression coverage.
- [x] 14. Record the fresh replay body's Lite state on the request and swap it onto the acceptance flag at replay time, so marker-stripped replays are not recorded as Lite acceptances while body-Lite replays re-establish trusted continuity, with regression coverage.
- [x] 15. Record the downstream-visible response id for Lite acceptances so suppressed-created replays keep trusted continuity on the id the client can reference, with unit and websocket-route regression coverage.
