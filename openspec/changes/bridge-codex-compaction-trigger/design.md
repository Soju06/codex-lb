## Context

Codex remote compaction v2 now appends a terminal `compaction_trigger` item to a normal `POST /backend-api/codex/responses` turn. The current proxy forwards that item as ordinary Responses input, which leaves the downstream compact collector with no `response.output_item.done` compaction item and causes the client-side remote compaction loop to fail.

The repo already has a working `/responses/compact` path that can fetch the compact summary from upstream and already has request normalization, OpenAI error envelopes, and SSE helpers. The missing piece is a Codex-route bridge that recognizes the trigger and emits the raw SSE shape the client expects.

## Goals / Non-Goals

**Goals:**
- Detect a terminal top-level `compaction_trigger` on `/backend-api/codex/responses`.
- Fail closed when a present trigger is duplicated or not final.
- Reuse the existing compact service path instead of building a second compaction implementation.
- Emit a raw SSE sequence that contains exactly one compaction output item and a terminal completion event.
- Leave `/responses/compact` and public `/v1/responses` behavior unchanged.

**Non-Goals:**
- Redesign the Codex compaction protocol.
- Change dashboard state, account routing, or compact persistence.
- Add new upstream dependencies.
- Alter the public OpenAI SSE contract for `/v1/responses`.

## Decisions

1. Add a small request-policy helper that strips a terminal top-level `compaction_trigger` from a `ResponsesRequest` input list and raises an OpenAI-shaped client payload error when placement is invalid.
   - Rationale: request normalization already lives in the request-policy layer, and this keeps validation close to the existing payload rules.
   - Alternative considered: detect the trigger inline in the route handler. Rejected because it would duplicate validation logic and make the request contract harder to test in isolation.

2. Special-case the Codex stream path inside `_stream_responses` only when `codex_session_affinity` is enabled.
   - Rationale: the behavior is specific to backend Codex turns, not public `/v1/responses`.
   - Alternative considered: add a separate route or move the branch into the service layer. Rejected because the route already owns the stream shaping and header plumbing.

3. Call the existing compact service and then synthesize a short SSE stream from the compact result.
   - Rationale: the compact service already handles account selection, retries, and direct upstream compact transport. The bridge only needs to translate the compact result into the Codex stream shape.
   - The synthetic stream should emit one `response.output_item.done` event containing a `compaction` item, then a `response.completed` event whose `response.output` contains the same single item, then `[DONE]`.
   - The synthetic response id should reuse the compact result id when present and otherwise fall back to the current request id.
   - Alternative considered: forward the trigger to upstream `/responses` and hope the model emits the right item. Rejected because the current failure shows that path returns no compaction output item.

4. Normalize the compact result into a single compaction item by preferring an explicit `output` item when present and otherwise deriving one from `compaction_summary.encrypted_content`.
   - Rationale: upstream compact payloads have varied slightly, but the Codex client only needs one canonical `compaction` item.
   - Alternative considered: accept any upstream shape and forward it as-is. Rejected because the downstream collector requires a single compaction output item.

5. Keep the bridge narrow.
   - `/responses/compact` remains unchanged.
   - `/v1/responses` remains unchanged.
   - The raw Codex route continues to bypass the public OpenAI SDK stream normalizer.

## Risks / Trade-offs

- [Risk] The compact result shape may drift and stop exposing either `output` or `compaction_summary`.
  - Mitigation: derive the stream item from both sources, prefer the canonical compact item shape, and add regression tests around the current result payload shape.

- [Risk] A synthetic response id derived from the request id is not the same as a true upstream response id.
  - Mitigation: only use the synthetic id when the compact result does not already provide one, and keep the raw Codex route behavior isolated from the public SDK contract.

- [Risk] Rejecting malformed trigger placement could surface new 400s for future client variants.
  - Mitigation: keep the validator narrow and targeted to top-level `compaction_trigger` placement, which is the only shape currently exercised by the Codex client.

## Migration Plan

No schema or data migration is required. Ship the validator and bridge together, verify the Codex stream regression tests, and roll back by reverting the route and request-policy changes if the synthetic compaction stream proves incompatible.

## Open Questions

- Should the synthetic Codex compaction stream ever include `response.output_item.added`, or is `response.output_item.done` plus `response.completed` sufficient for every current client?
- If upstream compact payloads begin returning additional output items, should the bridge preserve them or continue to enforce a single compaction item?
