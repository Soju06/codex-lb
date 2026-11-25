# Why

Upstream JSON mode (`text.format` of type `json_object`) rejects a request
unless an input message mentions JSON:

```
Response input messages must contain the word 'json' in some form to use
'text.format' of type 'json_object'.
```

Top-level `instructions` do not count. Upstream accepts a `developer` message
in `input` that mentions JSON, but rejects `system` messages there ("System
messages are not allowed").

Most clients put the JSON instruction in a `system` or `developer` message.
Instruction normalization moves those into top-level `instructions`, so the
only JSON mention leaves `input` and the request fails. #731 fixed this for
Chat Completions by keeping those messages in `input`. #950 moved them back
out, and #731's test was updated to expect that.

# What Changes

- For a request that uses `json_object`, a `system` or `developer` message that
  mentions JSON stays in `input` in its original position. A `system` message
  is sent with the `developer` role, because upstream rejects `system` there.
- Other `system` and `developer` messages still move into `instructions`.
- Requests without `json_object`, compact requests (they drop `text` before
  upstream) and Responses Lite input are unchanged.

# Capabilities

### Modified Capabilities

- `chat-completions-compat`: JSON mode keeps a JSON instruction message in
  `input` as a `developer` message.
- `responses-api-compat`: the same rule applies to Responses `input`.

# Impact

- Code: `app/core/openai/requests.py`
- Tests: request mapping, Responses validation, prompt-cache key derivation,
  and `/v1/chat/completions` plus `/v1/responses` upstream payloads.
