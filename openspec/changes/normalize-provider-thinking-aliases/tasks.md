## 1. Specs

- [x] 1.1 Add compatibility requirements for provider-specific thinking alias normalization.
- [x] 1.2 Validate OpenSpec changes.

## 2. Tests

- [x] 2.1 Add unit coverage for Chat Completions and shared Responses alias normalization.
- [x] 2.2 Add integration coverage for `/v1/chat/completions` with `enable_thinking`.

## 3. Implementation

- [x] 3.1 Normalize `thinking` / `enable_thinking` into `reasoning` before upstream forwarding.
- [x] 3.2 Ensure provider-specific alias fields are removed from forwarded upstream payloads.
