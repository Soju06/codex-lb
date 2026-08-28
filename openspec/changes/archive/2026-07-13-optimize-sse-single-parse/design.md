## Context

Perf audit finding: 5–7 JSON parses and 2–3 serializations per streamed event across four layers. The layers communicate via strings (yield types), so the minimal-risk step is eliminating redundant work WITHIN each layer plus pass-through where identity proves no mutation, without changing any generator's yield type.

## Goals / Non-Goals

**Goals:** one `json.loads` per event per layer; no serialization of unmodified events in the /v1 normalizers; byte-identical output.

**Non-Goals:** threading a parsed-event struct across layer boundaries (a follow-up that changes yield types); touching the core client's `_normalize_sse_event_block` (it already returns the original block unchanged when no rewrite applies); removing the mixin's canonical `format_sse_event` (it is the single serialization that guarantees stable framing, and downstream pass-through depends on it).

## Decisions

- **Payload-based validation** (`parse_sse_event_payload`) instead of caching parses by string: no cache keys, no invalidation, identical results by construction (the old `parse_sse_event(line)` parsed the same payload before validating).
- **Identity as the mutation oracle** in the public normalizer: the backfill branch and `_normalize_public_stream_payload` copy the dict whenever they change anything, so `normalized is parsed` proves no mutation. Chained after the mixin, the input block IS the mixin's canonical serialization, so pass-through is byte-identical to today's re-serialization.
- **Raw-block buffering** in the reasoning-summary normalizer so clean flushes replay upstream bytes instead of re-formatting every buffered delta.

## Risks / Trade-offs

- [A future mutating branch forgets to copy the dict and silently passes mutated payloads as "unmutated"] → the identity contract is documented at the check site; mutation-by-copy is already the established convention in both functions.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
