## 1. Implementation

- [x] 1.1 Omit `tools` from `ChatCompletionsRequest.to_responses_request()`
  when the client did not send the field.
- [x] 1.2 Keep an explicit client-sent empty `tools` array on the mapped
  Responses request.

## 2. Regression coverage

- [x] 2.1 Assert omitted chat `tools` stay out of `model_fields_set` and
  `to_payload()`.
- [x] 2.2 Assert explicit `tools: []` still appears on the mapped payload.
- [x] 2.3 Assert `/v1/chat/completions` does not forward synthesized
  `tools` to the Codex stream.

## 3. Validation

- [x] 3.1 Run the new mapping and chat-completions tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
