# Mask Codex ChatGPT previous-response WebSocket errors

## Why

Codex clients using the ChatGPT-backed `/backend-api/codex/responses` WebSocket path can still receive a raw upstream `previous_response_not_found` invalid-request error when the backend emits an error frame in a top-level error-field shape instead of the nested OpenAI error envelope shape already covered by the proxy.

Raw `previous_response_not_found` is a continuity-loss implementation detail. Surfacing it to Codex clients is harmful because clients may drop `previous_response_id` and resend full conversation history, causing runaway context growth.

## What Changes

- Treat top-level WebSocket error fields (`code`, `message`, `param`) as an upstream error payload for `type: "error"` frames.
- Reuse the existing continuity masking/rewrite path so Codex-native WebSocket clients receive `stream_incomplete`, not raw `previous_response_not_found`.
- Add regression coverage for `/backend-api/codex/responses` with a ChatGPT-style top-level previous-response-miss error frame.

## Impact

Codex ChatGPT-backend WebSocket sessions fail closed with a retryable continuity error when upstream loses a previous-response anchor. Existing nested error envelopes and public `/v1/responses` behavior remain unchanged.
