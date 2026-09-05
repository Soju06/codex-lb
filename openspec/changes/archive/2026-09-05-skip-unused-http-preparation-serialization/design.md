## Context

`_stream_responses_with_session` currently dumps a WebSocket-shaped request to measure size before choosing transport and then dumps the selected payload again. Explicit Python HTTP with payload tracing off does not consume either preparatory string; aiohttp serializes the transmitted body through its existing owner. Exact-body ablation measured preparation5.40→2.36 ms at1.06MB and46.02→18.85 ms at8.55MB.

## Goals / Non-Goals

**Goals:** Avoid unused full-body serialization while preserving transport decisions, exact HTTP body bytes and every enabled consumer.

**Non-Goals:** New serializers, schema/configuration, native IPC, payload-budget policy changes, WebSocket replay changes, archive/trace formats or broad lazy-object abstractions. Timing-field population is a separate branch.

## Decisions

- Determine whether the configured/overridden mode makes HTTP certain before building the WebSocket size-estimation string. Explicit HTTP and non-streaming HTTP need no WS size gate. Auto/WS-eligible modes must retain the current exact-byte budget check and fallback decisions.
- Keep selected payload construction/finalization unchanged. Materialize a full selected payload string only for consumers that need it: native request bytes, enabled raw upstream-payload trace, or the existing WebSocket send/budget path. The archive consumes the payload dictionary through its current owner. Do not defer work past a mutation boundary in a way that changes traced or sent contents.
- Prefer direct local conditional computation or reuse of an already-required string. Do not introduce a new global cache, serializer service or generalized lazy wrapper. Existing fallback/rewrite paths must refresh any materialized string when the payload changes.
- Extend existing core upstream-client tests with one representative large, non-ASCII/tool payload and a real local HTTP origin. Compare exact transmitted bytes/hash before and after the change, and measure the owning preparation's full-body dumps. Red must show unused serializations on pinned main; severing the optimization must fail the cost assertion while the real upstream witness still checks payload identity.
- Reuse existing tests for enabled raw tracing, native request-body consumers, non-streaming HTTP, and WS budget/fallback behavior. Add only missing representative consumer coverage, not one test for every control-flow branch. A bounded timing ablation is supplemental; avoid fragile elapsed-time performance thresholds in deterministic tests.

## Risks / Trade-offs

- [Removing a size calculation changes auto transport] → Retain the exact existing budget calculation for WS-eligible modes and exercise an existing threshold/fallback case.
- [A later fallback uses a stale string] → Review all `payload_json` consumers and invalidation points; preserve body identity after mutation.
- [Trace/native behavior regresses] → Existing active-consumer tests plus representative exact-body evidence.
- [Microbenchmark gains are generalized to all traffic] → Document preparation-only scope; retained CLI tool frames are about1.8KB and benefit much less.

## Migration Plan

No migration or configuration. Ordinary code rollback restores redundant work without changing the wire contract.

## Open Questions

None blocking. Choose final local variable types consistent with the existing JsonValue/Pydantic contracts, without broad refactoring.
