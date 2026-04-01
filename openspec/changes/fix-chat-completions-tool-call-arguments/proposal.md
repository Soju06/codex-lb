## Why
`/v1/chat/completions` currently rebuilds tool call arguments from streamed Responses events by blindly appending every tool-related payload. When upstream emits both incremental argument deltas and finalized snapshot events such as `response.function_call_arguments.done` or `response.output_item.done`, the adapter can duplicate `function.arguments` and return invalid JSON strings to OpenAI-compatible clients.

## What Changes
- Treat incremental tool-call argument deltas separately from finalized snapshot events in the Responses-to-Chat Completions adapter.
- Preserve the final tool call payload exactly once in both streaming and non-streaming `/v1/chat/completions` responses.
- Add regression coverage for mixed delta + done event sequences.

## Impact
- Restores OpenAI-compatible `tool_calls[].function.arguments` behavior for `/v1/chat/completions`.
- Does not change raw `/v1/responses` event forwarding or non-tool text handling.
