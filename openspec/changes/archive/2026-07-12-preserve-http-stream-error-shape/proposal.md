## Why

The codex-native backend stream endpoint `POST /backend-api/codex/responses` supports a compatibility
mode (`enforce_openai_sdk_contract`) where stream shaping may convert raw upstream events into
the OpenAI SDK-compatible Responses stream form. A bug report for PR #896 shows that when this
flag is `false`, upstream HTTP SSE error frames are still being normalized into OpenAI `response.failed`,
which mutates the raw backend stream shape.

## What Changes

- Keep the existing default normalization behavior when contract enforcement is enabled.
- Preserve raw upstream SSE event blocks when `enforce_openai_sdk_contract=False` for
  `/backend-api/codex/responses` HTTP streaming.
- Add a regression test for raw-error passthrough on the HTTP transport.
- Add OpenSpec coverage for the behavior under `responses-api-compat`.

## Impact

- Upstream `type: "error"` stream frames keep their native `sequence_number`/`error_type` fields when
  raw-stream mode is explicitly requested.
- OpenSpec now documents the contract-mode boundary so future changes preserve this behavior.
