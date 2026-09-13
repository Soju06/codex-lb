## 1. Implementation

- [x] 1.1 Declare `ResponsesRequest.input/tools`, `ResponsesCompactRequest.input`,
  `ResponsesTextFormat.schema`, `V1ResponsesRequest.input/messages/tools`,
  `V1ResponsesCompactRequest.input/messages` and
  `ChatCompletionsRequest.messages/tools/input` as passthrough JSON
  (`SkipValidation[SerializeAsAny[...]]`).
- [x] 1.2 Add field-level array checks for `tools` (shared `validate_tool_types`
  plus a chat field validator) and `messages` (V1, V1 compact, chat) so the
  error `param` still names the field.
- [x] 1.3 Attach passthrough fields directly in the compat converters instead of
  round-tripping them through `model_dump`.
- [x] 1.4 Accept the `/backend-api/codex/responses` body as `dict[str, Any]`.
- [x] 1.5 Reject passthrough fields nested deeper than 200 container levels in
  the same field validators (pydantic-core's serializer fails past ~250).

## 2. Regression coverage

- [x] 2.1 Golden corpus frozen from the pre-change models: `to_payload()` and
  `model_dump(mode="json")` bytes and `model_fields_set` identical for native,
  `/v1/responses` (input and messages), chat (messages, json_object,
  Responses-shaped) and compact requests.
- [x] 2.2 Non-array `tools` (`null`, string, number, object) rejected with 400
  `param=tools` on `/backend-api/codex/responses`, `/v1/responses` and
  `/v1/chat/completions`; non-array `messages` rejected with `param=messages`.
- [x] 2.3 `input` type check, tool-type alias normalization, input sanitation
  and omitted-`tools` propagation still run on the passthrough fields.
- [x] 2.4 Chat `refusal` of any non-string type and `tool_calls: null` accepted
  and mapped like the omitted key; model-level shape errors carry no param.
- [x] 2.5 Source-level guard: the raw body is not read after
  `normalize_responses_request_payload()` on the HTTP and WebSocket paths,
  plus a behavioural check that normalizing the same raw payload twice leaves
  it untouched (the WebSocket continuity wait re-normalizes).
- [x] 2.6 OpenAPI document still generates for the request models.
- [x] 2.7 Depth guard: every passthrough field at depth 300/5000 rejected with
  the field param, four HTTP routes and the Responses WebSocket return 400,
  depth at the limit still serializes; non-finite floats serialize as `null`.

## 3. Validation

- [x] 3.1 Run the request-model unit tests and the new route-level tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
