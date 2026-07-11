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
- [x] 10. Owner-forward signature integrity (Codex P2 on #1203): add a v2
  signature header (`x-codex-bridge-signature-v2`) computed over the
  forwarding dump actually posted (`model_dump_for_forwarding()`), with a
  version tag domain-separating it from the legacy digest. When the v2
  header is present the receiver verifies only v2, so a body rewritten in
  transit to inject an explicit empty tools list fails verification instead
  of re-marking `tools` as set on the owner. Regression test
  (fail-before/pass-after): tampered body is rejected with a 400
  invalid-signature error; honest round-trip still verifies.
- [x] 11. Rolling-upgrade compatibility (second Codex P2 on #1203): keep
  sending the legacy signature headers (plain-dump digest) so pre-v2 owners
  verify dual-signed forwards unchanged, and fall back to legacy
  verification only when the v2 header is absent (pre-v2 origin). Tests:
  new->new tamper rejection, new->old legacy-recompute equality, old->new
  fallback acceptance. ROLLOUT SHIM: legacy emission + fallback are a
  one-release shim — remove in a follow-up once fleets are homogeneous
  (grep `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`).
- [ ] 12. Follow-up (separate change, after one homogeneous release): drop
  the legacy v1 signature emission and the legacy fallback branch in
  `parse_forwarded_request`; verify v2 exclusively.
