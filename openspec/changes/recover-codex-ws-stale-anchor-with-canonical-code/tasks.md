## 1. Spec (this change)

- [x] 1.1 Redefine the Codex-native WebSocket stale-anchor sanitized signal as the canonical `previous_response_not_found` code (raw envelope and id stripped) in `responses-api-compat`.
- [x] 1.2 Scope the "never leak raw upstream errors" masking to the raw envelope and missing id, distinguishing the Codex-native WebSocket route (canonical code allowed) from public `/v1/responses` (`stream_incomplete` retained). Reconciles requirements 1104, 1134, 774, and 998.

## 2. Implementation (follows sign-off)

- [ ] 2.1 Change `_websocket_continuity_error_fields` so a stale-anchor failure emits `error.code = "previous_response_not_found"` with the raw upstream envelope and the missing `resp_...` id removed.
- [ ] 2.2 Preserve public `/v1/responses` `stream_incomplete` masking unchanged.

## 3. Coverage (follows sign-off)

- [ ] 3.1 Migrate the ~14 codex-native WebSocket stale-anchor tests from asserting `codex_previous_response_stale` + `"previous_response_not_found" not in payload` to the new contract: `error.code == "previous_response_not_found"` with no `resp_...` id and no raw upstream envelope.
- [ ] 3.2 Assert public `/v1/responses` WebSocket clients still receive `stream_incomplete`.
- [ ] 3.3 `openspec validate --specs` and focused WebSocket-surface tests pass.
