## Context

The compaction-trigger bridge converts a terminal `compaction_trigger` into a
short synthetic Responses stream. The current source emits the terminal item
and response events but omits the lifecycle events that normally establish the
response and output item first. A locally proven runtime patch fills that gap.

This change builds on the existing encrypted-item fidelity work: the
authoritative upstream `cmp_*` ID remains paired with its encrypted content.
The bridge does not invent an ID when upstream omits one because a generated ID
cannot prove the ciphertext was bound to it.

## Goals / Non-Goals

**Goals:**

- Emit a complete, deterministic Responses lifecycle for synthetic Codex
  compaction streams.
- Preserve the selected compaction item's ID, encrypted content, and terminal
  status across all terminal representations.
- Keep the implementation isolated to the existing synthetic stream helper.

**Non-Goals:**

- Change compact account selection, retries, affinity, or persistence.
- Generate replacement compaction item IDs.
- Change OpenAI-style `/v1/responses/compact`.
- Rework HTTP bridge stale-session cleanup, which current `main` already
  handles through the bounded session close path.

## Decisions

1. The synthetic stream emits the canonical order:
   `response.created`, `response.output_item.added`,
   `response.output_item.done`, `response.completed`, then `[DONE]`.
   Sequence numbers are `0` through `3`.

2. The added event presents the selected item with `status="in_progress"`.
   The done event and completed response reuse one terminal item mapping with
   `status="completed"` unless upstream supplied another non-empty terminal
   status.

3. Normalization preserves a non-empty upstream status alongside the existing
   ID and encrypted-content fidelity. Unknown fields remain excluded so the
   Codex-facing compact item contract stays narrow.

4. No replacement ID is generated. Remote compaction ciphertext can be bound to
   its upstream item ID, so synthesizing a different ID would recreate the
   replay failure this path is intended to prevent.

## Risks / Trade-offs

- [Risk] Older consumers see two additional non-terminal events.
  - Mitigation: both events are standard Responses lifecycle events, while the
    existing done and completed events remain unchanged in meaning.
- [Risk] An upstream item without a status gains `completed` in the synthetic
  stream.
  - Mitigation: the helper is building a terminal compact result, and the added
    event still overrides that view to `in_progress`.
