# Tasks

- [x] 1. Amend the OpenSpec requirement: strip the internal Lite header for non-native clients only; forward the lite signal for native Codex requests per transport.
- [x] 2. Preserve lite-shaped `input` in Responses request normalization (skip the instruction lift when an `additional_tools` item is present; never fold non-message typed developer items).
- [x] 3. Detect native lite requests from the inbound header or websocket client-metadata key and reconstruct the upstream signal: HTTP/compact header (initial + retry attempts) and websocket `client_metadata` key.
- [x] 4. Add regression coverage at the product paths: normalization unit tests (additional_tools, developer message, custom_tool_call / custom_tool_call_output), core upstream HTTP client (header + payload), HTTP responses route forwarding, and websocket bridge `response.create` forwarding; keep non-native strip coverage.
- [x] 5. Validate focused tests and OpenSpec artifacts.
