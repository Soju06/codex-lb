## 1. Bridge the trigger

- [x] 1.1 Add a request-policy helper that strips a terminal top-level `compaction_trigger` from a `ResponsesRequest` input list and rejects malformed placement with an OpenAI-shaped 400.
- [x] 1.2 Teach `/backend-api/codex/responses` to detect that helper result, call the existing compact service instead of the normal streaming path, and synthesize the raw SSE compaction stream from the compact result.

## 2. Cover the regressions

- [x] 2.1 Add a streaming integration test that verifies a valid terminal `compaction_trigger` yields exactly one `response.output_item.done` compaction item and a terminal `response.completed` event with the same item in `output`.
- [x] 2.2 Add a 400 regression test for malformed trigger placement, including duplicated and non-terminal trigger items.
- [x] 2.3 Add a direct Codex compact regression test that normalizes remote-compaction-v2 historical message output into one compact output item.

## 3. Validate the change

- [x] 3.1 Run the focused OpenSpec validation and targeted proxy tests for the new bridge path.
