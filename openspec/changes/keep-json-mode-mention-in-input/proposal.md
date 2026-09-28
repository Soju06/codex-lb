# Why

Upstream JSON mode (`text.format` of type `json_object`) rejects a request
unless a user input message mentions JSON:

```
Response input messages must contain the word 'json' in some form to use
'text.format' of type 'json_object'.
```

Top-level `instructions` and assistant messages do not count, and upstream
rejects `system` messages inside `input` ("System messages are not allowed").

Most clients put the JSON instruction in a `system` or `developer` message.
Instruction normalization moves those into top-level `instructions`, so the
only JSON mention leaves `input` and the request fails. #731 fixed this for
Chat Completions by keeping those messages in `input`. #950 moved them back
out, and #731's test was updated to expect that.

# What Changes

- Keep moving `system`/`developer` messages into `instructions`.
- When the request uses `json_object`, a moved message mentioned JSON, and no
  user input message does, add the text part `Respond in JSON.` to the start
  of the first user message. With no user message, append a user message
  with only that note.
- Compact requests are unchanged; they drop `text` before upstream.

# Capabilities

### Modified Capabilities

- `chat-completions-compat`: JSON mode maps instruction messages to
  `instructions` and keeps a JSON mention in `input`.
- `responses-api-compat`: the same rule applies to Responses `input`.

# Impact

- Code: `app/core/openai/requests.py`
- Tests: request mapping, Responses validation, prompt-cache key derivation,
  and `/v1/chat/completions` plus `/v1/responses` upstream payloads.
