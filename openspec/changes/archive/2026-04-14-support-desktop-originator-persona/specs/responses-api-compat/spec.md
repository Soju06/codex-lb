## MODIFIED Requirements
### Requirement: Auto websocket fallback remains narrow and explicit
When automatic upstream transport selection prefers websocket, the service MUST only downgrade to HTTP automatically on `426 Upgrade Required`. Handshake failures such as `403 Forbidden` or `404 Not Found` MUST surface as upstream errors instead of silently falling back to HTTP.

#### Scenario: first-party chat/Desktop originators count as native Codex websocket signals
- **WHEN** a Responses request arrives with `originator: "codex_atlas"` or `originator: "codex_chatgpt_desktop"`
- **THEN** the service treats that request as a native Codex websocket signal for automatic upstream transport selection
- **AND** it applies the same native Codex transport path it already applies to `codex_cli_rs` or `Codex Desktop`
