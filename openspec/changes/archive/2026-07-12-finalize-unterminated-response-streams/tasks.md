## 1. Spec Delta

- [x] 1.1 Add a `responses-api-compat` requirement for raw streaming Responses
  attempts that end before a terminal event.
- [x] 1.2 Cover upstream EOF and downstream client cancellation separately.

## 2. Implementation

- [x] 2.1 Track terminal SSE events in the raw Responses streaming path.
- [x] 2.2 Emit a synthetic `response.failed` for upstream EOF without a terminal
  event.
- [x] 2.3 Record downstream disconnect before terminal as a non-account-health
  client-side failure.

## 3. Verification

- [x] 3.1 Add integration coverage for upstream EOF without a terminal event.
- [x] 3.2 Run targeted stream integration tests.
- [x] 3.3 Run the full `test_proxy_api_extended.py` integration file.
- [x] 3.4 Run `uv run openspec validate finalize-unterminated-response-streams --strict`.
- [x] 3.5 Run `uv run openspec validate --specs`.
