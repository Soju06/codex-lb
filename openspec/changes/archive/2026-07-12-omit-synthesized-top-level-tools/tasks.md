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
  version tag domain-separating it from the legacy digest. A validating v2
  signature proves the received body was not rewritten in transit, so an
  injected explicit empty tools list fails v2 instead of re-marking `tools`
  as set on the owner. Regression test (fail-before/pass-after): tampered
  body rejected 400 when v2 is the operative signature; honest round-trip
  still verifies.
- [x] 11. Rolling-upgrade compatibility (second Codex P2 on #1203): keep
  sending the legacy signature headers (plain-dump digest) so pre-v2 owners
  verify dual-signed forwards unchanged. Tests: new->old legacy-recompute
  equality, old->new fallback acceptance. ROLLOUT SHIM: legacy emission +
  fallback are a one-release shim — remove in a follow-up once fleets are
  homogeneous (grep `ROLLOUT SHIM` / `HTTP_BRIDGE_SIGNATURE_V2_HEADER`).
- [x] 12. Spoofed-v2 resilience (third Codex P2 on #1203): treat v2 as
  authoritative only when it VALIDATES — an absent or invalid v2 header
  falls through to legacy verification, and the forward is rejected only
  when neither digest verifies, so a garbage v2 header planted by an
  external client (relayed verbatim by pre-v2 origins) cannot deny an
  honestly legacy-signed forward. Updated origins strip inbound
  `x-codex-bridge-*` headers. Documented shim residual: a body-only
  `"tools": []` injection into a dual-signed forward downgrades to the
  legacy digest and verifies (exactly the pre-v2 strength); locked by test
  and reverted to strict rejection when the shim is removed. Tests
  (fail-before/pass-after): spoofed garbage v2 + valid legacy accepted via
  fallback; client-supplied bridge headers dropped by the origin; generic
  tamper still rejected with both digests broken.
- [x] 13. Deferred out of this change's scope (tracked as a separate follow-up change): drop
  the legacy v1 signature emission and the legacy fallback branch in
  `parse_forwarded_request`; verify v2 exclusively (flips the documented
  shim-residual test back to strict rejection).
