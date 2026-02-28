## 1. Transport Bridge

- [x] 1.1 Route `/claude/v1/messages` through `ProxyService.stream_responses` with API key limit enforcement.
- [x] 1.2 Keep `/claude-sdk/v1/messages` behavior unchanged.

## 2. Compatibility Translation

- [x] 2.1 Add Anthropic -> Responses request translator for messages/tools/tool_choice.
- [x] 2.2 Add ChatCompletion/OpenAI error -> Anthropic response mapping.
- [x] 2.3 Add Anthropic SSE event emitter for `stream=true` on bridge route.

## 3. Validation

- [x] 3.1 Update integration tests for `/claude/v1/messages` to validate Codex bridge transport.
- [x] 3.2 Add unit tests for translation and error mapping helpers.
- [x] 3.3 Add startup compatibility endpoint coverage for Claude Desktop bootstrap routes.
- [ ] 3.4 Run `openspec validate --specs` (openspec CLI unavailable in this environment).
- [x] 3.5 Run targeted pytest suites for anthropic bridge + desktop startup path.
