## 1. Payload Sanitization

- [x] 1.1 Add `prompt_cache_retention` to Responses unsupported-upstream field stripping
- [x] 1.2 Add `temperature` to Responses unsupported-upstream field stripping
- [x] 1.3 Ensure stripping applies to both `ResponsesRequest` and `ResponsesCompactRequest`

## 2. Tests

- [x] 2.1 Add unit regression test for `ResponsesRequest.to_payload()` stripping behavior
- [x] 2.2 Add unit regression test for `ResponsesCompactRequest.to_payload()` stripping behavior
- [x] 2.3 Add chat-mapping regression test to verify `temperature` is not forwarded upstream

## 3. Specs and Compatibility Docs

- [x] 3.1 Add `responses-api-compat` spec delta for stripping known unsupported advisory parameters
- [x] 3.2 Update `refs/openai-compat-test-plan.md` support matrix with parameter compatibility note
- [x] 3.3 Run `openspec validate --specs`
