## 1. Validation updates

- [x] 1.1 Reject `input_file.file_id` in Responses request validation with a stable `param` (e.g., `input`).
- [x] 1.2 Reject chat `file` parts that include `file_id` with a stable `param` (e.g., `messages`).

## 2. Error envelope alignment

- [x] 2.1 Ensure invalid request errors for file_id rejections use message "Invalid request payload" and `invalid_request_error` type/code.
- [x] 2.2 Decide and document the `param` value for chat file_id rejections (messages vs omit), then align validation to it.

## 3. Tests and documentation

- [x] 3.1 Update integration/unit tests to assert file_id rejections for Responses and Chat.
- [x] 3.2 Update compatibility docs and live check expectations to mark file_id unsupported.
