# Why

The Responses and Chat Completions request models declared their passthrough
fields (`input`, `tools`, `messages`, `text.format.schema`) as the recursive
`JsonValue` union. The body is already `json.loads` output, so pydantic walked
every node a second time (a Python-level `Mapping` ABC `isinstance` per dict)
and deep-copied the tree; the OpenAI-compatible `/v1/responses` and
`/v1/chat/completions` paths then re-dumped and re-validated the same tree
(three full passes per request). The `/backend-api/codex/responses` route also
validated the raw body as `dict[str, JsonValue]` before any model saw it. On
the production profile this is ~1.7-2.8% of GIL CPU for no information gain.

# What Changes

- Declare the passthrough fields as opaque JSON (`SkipValidation` +
  `SerializeAsAny`): pydantic no longer deep-validates or coerces their nested
  values, and serializes them by runtime type. Forwarded payload bytes are
  unchanged (pinned by a golden corpus frozen from the previous models).
- Keep the top-level shape checks the `list` types used to provide, as field
  validators: `tools` must be an array, `messages` must be an array when
  present, `input` must be a string or array. Violations still return HTTP 400
  `invalid_request_error` with `param` naming the field.
- The compat converters (`V1ResponsesRequest.to_responses_request`,
  `V1ResponsesCompactRequest.to_compact_request`,
  `ChatCompletionsRequest.to_responses_request`) attach the passthrough fields
  directly instead of round-tripping them through `model_dump`.
- `/backend-api/codex/responses` accepts the body as `dict[str, Any]`; the
  request models validate it immediately afterwards as before.
- Observable leniency: chat message keys the mapping never inspected for type
  (e.g. `refusal: null`, `name`) are no longer rejected by the `OpenAIMessage`
  TypedDict; the mapping rules (object messages, string role, supported roles,
  content/tool-call rules) still reject malformed messages.

# Capabilities

### Modified Capabilities

- `responses-api-compat`: passthrough request fields are shape-checked, not
  deep-validated; nested values forward byte-for-byte.
- `chat-completions-compat`: same contract for `messages`/`tools`/`input` on
  `/v1/chat/completions`.

# Impact

- Per 79 KB request (local bench): native `model_validate` 1.30 -> 0.25 ms,
  compat chain 3.13 -> 0.29 ms, chat chain 3.94 -> 0.80 ms, `model_dump`
  0.84 -> 0.25 ms, raw-body validation 1.04 -> 0.001 ms.
- Error envelopes for non-array `tools`/`messages` keep status 400, type
  `invalid_request_error`, message `Invalid request payload` and `param`; only
  pydantic's internal error type changes (`list_type` -> `value_error`).
- The normalized request now holds the client's own nested objects rather than
  validated copies. Nothing reads the raw body after normalization on the HTTP
  or WebSocket paths; a source-level regression test pins that.
- Non-JSON Python inputs (tuples, mapping proxies) are no longer coerced; no
  internal producer passes them (all inbound data is `json.loads` output).
