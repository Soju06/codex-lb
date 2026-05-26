## Why

Chat Completions JSON mode currently fails when the explicit JSON instruction appears in a `system` or `developer` message. The chat-to-Responses mapper moves those messages into `instructions`, but the upstream JSON-mode validation requires the JSON marker to remain in the input context.

OpenAI-compatible JSON mode allows clients to instruct JSON output via conversation messages, including system messages, so codex-lb should not require clients to duplicate that instruction in a user message.

## What Changes

- Preserve `system` and `developer` messages as Responses `input` role messages only for Chat Completions requests with `response_format.type == "json_object"`.
- Keep the existing `instructions` merge behavior for non-JSON-mode requests and for `json_schema` structured outputs.
- Include preserved instruction-role input text in derived prompt-cache keys.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`: JSON object response format mapping MUST keep instruction-role messages in Responses `input`.

## Impact

- **Code**: `app/core/openai/chat_requests.py`, `app/core/openai/message_coercion.py`, `app/modules/proxy/service.py`
- **Tests**: focused unit coverage for chat mapping and prompt-cache key derivation
- **Behavior**: `/v1/chat/completions` accepts JSON mode requests whose JSON instruction is in a `system` or `developer` message.
