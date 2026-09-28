## 1. Implementation

- [x] 1.1 For a `json_object` request, keep a `system`/`developer` message that
  mentions JSON in `input` as a `developer` message, in its original position.
- [x] 1.2 Leave compact requests, Responses Lite input, non-JSON-mode
  requests, and instruction messages that do not mention JSON unchanged.

## 2. Regression coverage

- [x] 2.1 `/v1/chat/completions` and `/v1/responses` forward the JSON
  instruction in `input` as a `developer` message.
- [x] 2.2 A non-JSON format, compact requests, Lite input, and instruction
  messages without a JSON mention are hoisted as before.
- [x] 2.3 The forwarded input is the same on every turn, so the prompt-cache
  key stays stable as the thread grows.

## 3. Validation

- [x] 3.1 Run the mapping, request, cache-key, and integration tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
