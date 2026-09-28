## 1. Implementation

- [x] 1.1 When JSON mode is requested and a hoisted instruction message
  mentioned JSON but no user input message does, add `Respond in JSON.` to
  the first user message, or append a note-only user message.
- [x] 1.2 Leave compact requests, Responses Lite input, non-JSON-mode
  requests, and requests with no JSON mention unchanged.

## 2. Regression coverage

- [x] 2.1 `/v1/chat/completions` and `/v1/responses` forward the note in
  `input` for a JSON instruction that only a system or developer message
  carried.
- [x] 2.2 A user JSON mention, a missing JSON mention, a non-JSON format,
  Lite input, and compact requests add no note.
- [x] 2.3 The note is added once, keeps non-text parts, and keeps the
  prompt-cache key stable across turns.

## 3. Validation

- [x] 3.1 Run the mapping, request, cache-key, and integration tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
