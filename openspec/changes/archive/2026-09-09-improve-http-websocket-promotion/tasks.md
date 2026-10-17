## Implementation
- [x] Recognize multi-turn evidence and remove identity-only native HTTP pin.
- [x] Add stable soft locality without inventing hard continuity or trimming unproved history.
- [x] Enable Chat Completions bridge for streaming and collected responses.
- [x] Expose admission/bypass reasons and connection reuse counters.

## Validation
- [x] Exercise smart/native/history/tool/conversation policy and real HTTP endpoint connector behavior.
- [x] Verify repeated Chat and Responses turns reuse WS, preserve full input and isolate unrelated/API-key scopes.
- [x] Verify outage, image/size, explicit-policy, source routing, and cleanup regressions.
- [x] Run focused tests, lint/type/architecture, strict OpenSpec validation; sync and archive.
