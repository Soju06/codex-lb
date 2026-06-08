## 1. Implementation

- [x] 1.1 Preserve `system`/`developer` messages in `input` for Chat Completions `json_object` response format.
- [x] 1.2 Keep existing `instructions` mapping for non-JSON-mode requests and `json_schema`.
- [x] 1.3 Include preserved instruction-role input in derived prompt-cache keys.

## 2. Tests and Specs

- [x] 2.1 Add regression coverage for `json_object` with instruction-role messages.
- [x] 2.2 Add prompt-cache key coverage for instruction-role input.
- [x] 2.3 Update the `chat-completions-compat` spec for the JSON-mode mapping exception.
