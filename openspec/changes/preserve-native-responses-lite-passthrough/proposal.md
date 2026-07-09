# Change: Preserve native Responses Lite passthrough

## Why

GPT-5.6 models with `use_responses_lite: true` (for example `gpt-5.6-sol`, `tool_mode: code_mode_only`) do not send tools in the top-level `tools` field. Codex ≥ 0.144.0 instead prepends an input item `{"type": "additional_tools", "role": "developer", "tools": [...]}` and marks the request with the internal `x-openai-internal-codex-responses-lite` header (HTTP) or the `ws_request_header_x_openai_internal_codex_responses_lite` client-metadata key (websocket).

codex-lb broke this contract twice (issue #1157):

1. `_normalize_responses_input_instructions()` treated every `developer`/`system` input item as instruction text. The `additional_tools` item has no `content`, so it was silently dropped and the upstream model received no shell/filesystem tools ("I can't inspect the repository because no shell/filesystem tool is available in this session.").
2. The `strip-internal-responses-lite-header` change removes the Lite header from all upstream requests, so even an intact tool bundle would not be processed in lite mode upstream.

## What Changes

- Skip the instruction lift entirely for lite-shaped requests (array `input` containing an `additional_tools` item) so the upstream payload matches what Codex constructed, including the developer instructions message, `custom_tool_call`, and `custom_tool_call_output` items.
- Detect a native Codex lite request from a truthy inbound header or the websocket client-metadata key, and reconstruct the upstream signal per transport: header for HTTP responses/compact (initial and retry attempts), client-metadata key for websocket `response.create`.
- Keep stripping the header for non-native clients (OpenAI SDK fingerprints), preserving the protection that motivated `strip-internal-responses-lite-header` (upstream rejects the header on models that do not support lite; non-native clients cannot be trusted to send it only for lite-capable models).
- Regression coverage at the product paths: HTTP responses route, core upstream HTTP client, and the websocket bridge.

Out of scope: advertising or rewriting `use_responses_lite` / `tool_mode` model metadata (served verbatim from upstream model data), grammar validation of the lite `exec` tool bundle, and Chat Completions surfaces.

## Impact

- Affected specs: `responses-api-compat` (modifies "Internal Responses Lite header is not forwarded upstream"; adds "Responses Lite input payloads pass through unmodified").
- Affected code: `app/core/openai/requests.py`, `app/core/clients/proxy.py`, `app/modules/proxy/_service/{compact,response_create,http_bridge/streaming,streaming/mixin}.py`.
- Native Codex clients regain shell/filesystem tools on GPT-5.6 lite models; non-native client behavior is unchanged.
- Fixes #1157.
