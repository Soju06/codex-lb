## Implementation
- [x] Guard terminal socket replay by zero response events, no buffered output, sole pending ownership and existing continuity/durable fences.
- [x] Reconnect once with original-account preference; report zero_event_replay outcome.
- [x] Cover clean/transient/transport loss, second loss, positive events and continuity.
- [ ] Run lint, typecheck, focused unit tests, bridge integration and strict OpenSpec validation.
