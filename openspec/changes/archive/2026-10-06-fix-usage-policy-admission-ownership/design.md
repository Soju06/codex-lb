## Context

See proposal.md for the reproduced failures. The pure usage evaluator, authoritative owner snapshot, and transport-specific lifecycle boundaries remain the design. Local cache generations identify acknowledged policy changes; cross-replica TTL behavior remains unchanged.

## Goals / Non-Goals

Complete final admission fences and resource ownership using existing selection and WebSocket finalization machinery. Preserve disabled policies, error precedence, committed affinity, and historical migration paths. Do not add a routing framework, policy revision column, settings, or migration.

## Decisions

- Check generation under the final probe-commit lock before consuming the quiet interval. Carry the successful selection generation to the caller's final seed-persistence fence; an initial generation cannot describe a retried successful attempt.
- Preserve committed process affinity on late invalidation. Compensating deletion could break a sibling that already observed the owner.
- Publish an owned WebSocket finalization task before awaiting rejected-frame cleanup. Reuse scope cleanup tracking rather than leave a removed frame reachable only from a cancelled stack.
- Reuse cancellation-deferring selection resource release at late exits. Preserve cancellation even when cleanup reports an error.

## Risks / Trade-offs

- Policy invalidation after immutable seed persistence produces a retryable local error instead of undoing affinity. This matches late sticky persistence behavior.
- Cleanup must preserve frame-specific errors and exactly-once settlement while allowing overlapping responses to finish; regression tests exercise the public request paths and real resource accounting.
- Generation checks remain local observation boundaries, not a distributed policy lock. Authoritative existing-owner dispatch reads remain necessary.

## Migration Plan

Deploy as a normal application update. No schema changes; keep all published revision IDs and both historical merge paths. Validate the single-head graph against current upstream main and retain historical upgrade regressions.
