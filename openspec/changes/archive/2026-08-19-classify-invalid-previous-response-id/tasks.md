## 1. Regression Coverage

- [x] 1.1 Add a Codex-native route regression using the exact production frame (`invalid_request_error`, no `code`/`param`, ``Invalid `previous_response_id`.``) and verify it fails by exposing the raw 400 before implementation.

## 2. Classification Fix

- [x] 2.1 Extend the shared previous-response classifier with the exact parameterless upstream message, normalize the code-less nested frame consistently at the rewrite call, and reject a different named parameter or unrelated invalid-request message.
- [x] 2.2 Verify the route regression passes and the existing canonical stale-anchor recovery tests remain green.

## 3. Compatibility Boundaries

- [x] 3.1 Cover the exact observed frame in the self-contained full-resend replay path and confirm the replay drops `previous_response_id`.
- [x] 3.2 Cover the exact observed frame on public `/v1/responses` and confirm it retains generic `stream_incomplete` masking.
- [x] 3.3 Add focused classifier cases for the observed shape and false-positive boundaries.
- [x] 3.4 Add the stable failure-mode and recovery example to the existing `responses-api-compat` context documentation.

## 4. Verification

- [x] 4.1 Run focused OpenAI error and direct WebSocket route tests, then the relevant proxy architecture and formatting/lint/type checks.
- [x] 4.2 Run strict OpenSpec validation, the repository's proportionate final gate, and review the final diff for unrelated changes.
