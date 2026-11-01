## Why

Chat Completions function tools are non-strict by default. The chat-to-Responses
converter currently preserves an omitted `strict` field, allowing Responses to
normalize the schema into strict mode instead. Optional tool arguments can then
become required even though the client did not request strict sampling.

## What Changes

- Make the non-strict Chat Completions default explicit when converting function
  tools on requests with chat messages.
- Preserve explicit boolean strictness and the original parameter schema.
- Keep native Responses requests and Responses-shaped chat passthrough unchanged.
- Cover converter output and streaming/non-streaming HTTP forwarding offline.

## Impact

- Affected capability: `chat-completions-compat`.
- Affected code: `app/core/openai/chat_requests.py`.
- No new configuration, dependencies, endpoint, or authentication changes.

Tracking issue: https://github.com/Soju06/codex-lb/issues/2691
