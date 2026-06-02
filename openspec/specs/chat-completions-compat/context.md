# Chat Completions Compatibility Context

## Purpose and Scope

This capability aligns `POST /v1/chat/completions` with OpenAI’s expectations by mapping chat requests to Responses, preserving streaming behavior, and returning OpenAI-compatible error envelopes.

See `openspec/specs/chat-completions-compat/spec.md` for normative requirements.

## Rationale and Decisions

- **Mapping to Responses:** Chat Completions are derived from the Responses stream to keep behavior consistent across endpoints.
- **Responses-shaped passthrough:** Some OpenAI-compatible clients send `input` through `/v1/chat/completions`; those payloads are treated as Responses requests after chat-level validation.
- **Strict role/content rules:** System/developer messages are text-only; user content parts are validated for supported types.
- **Usage streaming:** When `stream_options.include_usage` is enabled, usage appears in the final chunk while earlier chunks include `usage: null`.
- **Obfuscation passthrough:** `stream_options.include_obfuscation` is forwarded to upstream when present.

## Constraints

- Oversized image data URLs (>8MB) are dropped from user inputs.
- Audio input (`input_audio`) is not supported and is rejected.
- Built-in Responses tools are preserved only on the Responses-shaped passthrough path; ordinary chat-message payloads keep the narrower chat tool policy.
- `response_format` is translated to `text.format` with JSON schema validation.

## Failure Modes

- **Upstream stream failure:** Emit an error chunk, then terminate with `data: [DONE]`.
- **Non-stream failures:** Return an OpenAI error envelope with 5xx status.
- **Invalid content types:** Reject with `invalid_request_error`.

## Examples

Streaming request with usage:

```json
{
  "model": "gpt-5.2",
  "messages": [{"role": "user", "content": "hi"}],
  "stream": true,
  "stream_options": { "include_usage": true }
}
```

Responses-shaped chat request with a built-in tool:

```json
{
  "model": "gpt-5.2",
  "input": [{"role": "user", "content": [{"type": "input_text", "text": "Generate an image."}]}],
  "tools": [{"type": "image_generation"}],
  "tool_choice": {"type": "image_generation"}
}
```

## Operational Notes

- Streaming chunk mapping is validated in unit tests.
- Integration tests cover include_usage and tool call finish reasons.
