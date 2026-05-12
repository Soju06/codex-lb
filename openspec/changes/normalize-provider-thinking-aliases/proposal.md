## Why

Some OpenAI-compatible clients reuse provider-specific reasoning controls when pointed at `codex-lb`. In particular, Qwen/DeepSeek-style `enable_thinking` and Anthropic-style `thinking` fields can leak through the Chat Completions and Responses compatibility layers and reach the upstream ChatGPT backend unchanged, which causes avoidable upstream validation failures.

## What Changes

- Normalize provider-specific thinking aliases into the existing `reasoning` payload before upstream forwarding.
- Apply that normalization to Chat Completions request mapping and to the shared OpenAI-compatible Responses payload sanitation path.
- Drop the original provider-specific alias fields from forwarded upstream payloads.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`
- `responses-api-compat`
