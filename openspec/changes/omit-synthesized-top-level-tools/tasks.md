# Tasks

- [x] 1. Add OpenSpec requirements: omit client-omitted request fields (`tools` and audited siblings), forward client tool entries byte-preserved, keep canonicalization cache-affinity-only.
- [x] 2. `ResponsesRequest.to_payload()`: pop `tools` when the client did not send the field; preserve explicit client-sent `[]`.
- [x] 3. Audit `tool_choice` / `parallel_tool_calls` for the same default-injection pattern (result: `None` defaults already dropped by `exclude_none`; no change needed).
- [x] 4. `V1ResponsesRequest.to_responses_request()`: propagate `tools` omission into the converted `ResponsesRequest`.
- [x] 5. Remove `_canonicalize_tools` from the wire path; expose `canonicalized_tools()` and use it only in the `_tools_hash` affinity/observability consumer.
- [x] 6. Regression tests (fail-before/pass-after): Lite websocket frame and HTTP-bridge body carry no top-level `tools` key; client-sent reserved namespace tool reaches the upstream frame byte-identical; explicit `[]` still forwarded; affinity hash stays order-insensitive.
- [x] 7. Run focused tests, lint, type check, and strict OpenSpec validation.
