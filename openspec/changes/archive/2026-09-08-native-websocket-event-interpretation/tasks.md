# Tasks: native-websocket-event-interpretation

- [x] Audit current Python consumers and intervening changes through the latest main.
- [x] Add protocol capability, request opt-in, raw payload IPC, and Rust classification.
- [x] Preserve raw WebSocket text/values, type precedence, aliases and HTTP bridge framing.
- [x] Reuse native payloads at WebSocket/bridge consumers and remove unreachable duplicate code.
- [x] Verify direct/routed Responses, opaque Live contract, unsupported/large JSON and invalid metadata.
- [x] Add shared fixtures and real-helper regressions to CI; validate Rust/Python checks.
- [x] Record reproducible benchmark, ownership table and legacy-retirement criteria; sync specs.
