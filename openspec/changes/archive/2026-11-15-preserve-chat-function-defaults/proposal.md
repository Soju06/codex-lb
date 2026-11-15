## Why

Chat Completions defaults function tools to non-strict mode. Forwarding an
omitted flag to Responses changes its meaning and can make optional arguments
required (upstream issue #2691).

## What Changes

- Emit `strict: false` for omitted or null function strictness when converting
  non-empty chat messages, including accepted flat function definitions.
- Preserve explicit values, parameter schemas, and client-owned tool objects.
- Keep native Responses and Responses-shaped chat passthrough semantics intact.
- Verify converter behavior and streaming/non-streaming HTTP forwarding.

## Impact

- Capability: `chat-completions-compat`.
- Code: `app/core/openai/chat_requests.py`.
- No configuration or dependency changes.

Tracking: https://github.com/Soju06/codex-lb/issues/2691
