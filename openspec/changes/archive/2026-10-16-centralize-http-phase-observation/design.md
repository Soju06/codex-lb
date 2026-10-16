## Context

See the round 5 proposal at `docs/reviews/pr-2112/proposals/round-5.md` for the invariant, owner, catch map, and sibling inventory.

## Goals / Non-Goals

**Goals:** A total per-frame phase operation owns both pending observations and relay authorization; normalization preserves the source eligibility it needs.

**Non-Goals:** Change TTFT classification, error/wire policy, retry ownership, storage, metrics, or native IPC.

## Decisions

- Expand the existing phase operation into the combined owner rather than add a second relay-readiness predicate at its caller. Both first and later frames use it. A qualifying token can bypass parsing only after first-upstream is observed; existing canonical relay rules exclude created/lifecycle events.
- Preserve an immutable upstream-keepalive fact in the existing parsed SSE carrier when normalization changes the frame. Derive it from source payload/header before rewriting, and inherit it when wrapping an existing carrier.
- Retain normal error identity provenance separately: a synthesized response ID does not make genuine upstream activity local.
- Native HTTP errors already retain their original text and request Python normalization; no Rust or IPC change is needed.
- Extend the existing route table and owner property test, using real normalizers and generated event sequences. The route remains the primary persistence/metrics regression.

## Risks / Trade-offs

- A new owner could force later token parsing → retain parse-spy and byte assertions through that owner.
- Rewriting could discard source identity → test the actual normalizer, not manually prepared metadata.
