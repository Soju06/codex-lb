## Why

The Chat Completions adapter currently treats an upstream Responses iterator
that reaches EOF without a terminal event as success. Streaming callers receive
content without the required `data: [DONE]` marker, while non-streaming callers
receive a successful `chat.completion` whose partial text has
`finish_reason=stop`. The public Responses adapter already classifies the same
condition as `upstream_stream_truncated`.

## What Changes

- Detect upstream EOF before any `response.completed`,
  `response.incomplete`, `response.failed`, or `error` event.
- Emit an OpenAI error chunk followed by `data: [DONE]` for streaming Chat
  Completions.
- Return an OpenAI error envelope that maps to HTTP 502 for non-streaming Chat
  Completions.
- Preserve explicit terminal/error handling, usage/tool-call finalization, and
  upstream generator cleanup.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`: define deterministic truncation behavior for
  streaming and collected Chat Completions.

## Impact

- Affected code: `app/core/openai/chat_responses.py`
- Affected route: `POST /v1/chat/completions`
- Affected tests: Chat response mapping and proxy Chat Completions integration
- Compatibility: malformed upstream termination changes from false success to a
  stable OpenAI server-error envelope
