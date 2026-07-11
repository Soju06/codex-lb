# Tasks

- [x] 1. Add OpenSpec requirements: omit client-omitted request fields (`tools` and audited siblings), forward client tool entries byte-preserved, keep canonicalization cache-affinity-only.
- [x] 2. `ResponsesRequest.to_payload()`: pop `tools` when the client did not send the field; preserve explicit client-sent `[]`.
- [x] 3. Audit `tool_choice` / `parallel_tool_calls` for the same default-injection pattern (result: `None` defaults already dropped by `exclude_none`; no change needed).
- [x] 4. `V1ResponsesRequest.to_responses_request()`: propagate `tools` omission into the converted `ResponsesRequest`.
- [x] 5. Remove `_canonicalize_tools` from the wire path; expose `canonicalized_tools()` and use it only in the `_tools_hash` affinity/observability consumer.
- [x] 6. Regression tests (fail-before/pass-after): Lite websocket frame and HTTP-bridge body carry no top-level `tools` key; client-sent reserved namespace tool reaches the upstream frame byte-identical; explicit `[]` still forwarded; affinity hash stays order-insensitive.
- [x] 7. Run focused tests, lint, type check, and strict OpenSpec validation.
- [x] 8. Follow-up (#1184 residual gaps, salvaged from #1187): extract
  `ResponsesRequest.model_dump_for_forwarding()` and use it for the
  multi-instance owner-forward body (`HTTPBridgeOwnerClient`) and
  model-source Responses egress (`_source_responses_response`) so omission
  survives re-serialization hops and the owner instance does not re-mark
  `tools` as set.
- [x] 9. Regression tests (fail-before/pass-after) at the two residual
  surfaces: owner-forward JSON body carries no `tools` key for a request
  that omitted it (and the forwarded signature still verifies), and the
  source-bound Responses payload carries no `tools` key.
