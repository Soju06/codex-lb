## Context

The failing subtask input has two messages and a `function_call_output` with `id`, `name`, `namespace`, and string `output`, without `call_id`. Its local ID is the only unresolved reference across all five sources. A harmless direct request with this shape completed successfully at the configured endpoint. Codex protocol `ResponseItem::FunctionCallOutput` has optional `call_id`, `name`, and `namespace`; its `named_unpaired_function_call_output_round_trips_without_call_id` test explicitly covers this shape. See https://github.com/openai/codex/blob/main/codex-rs/protocol/src/models.rs. Strict third-party providers may reject it (https://github.com/openai/codex/issues/45914).

## Goals / Non-Goals

Accept self-contained subtask notifications through source pools with one consistent, strict predicate. Preserve all original wire input to retain semantics and cache prefixes. Do not fabricate calls, convert tool output into higher-priority messages, alter subscription replay, change credentials or migrate database state.

## Decisions

Add a predicate restricted to named standalone function outputs with absent `call_id`. Validate exact known fields, optional bookkeeping ID and namespace, existing internal metadata, and existing self-contained tool content checks. Ownership extraction skips validated standalone items. Direct-source portability excludes them only in its classification copy; the wire body is untouched. A null, blank, malformed or real `call_id` does not qualify. Ordinary results retain call ownership and all other reference checks remain authoritative.

Removing just the ID is insufficient: the existing replay predicate also requires a matching call. Extending that general predicate would unintentionally broaden subscription replay. Inventing a call or rewriting the output would change model context without necessity at the affected endpoint.

## Risks / Trade-offs

- Provider compatibility varies: this routing allowance does not claim every third-party provider accepts Codex standalone outputs; upstream errors remain visible.
- Future opaque fields could bind an account: strict field and content validation declines unknown shapes.
- Multiple replicas must agree after rollout: use the existing HA surge deployment and verify all three serving replicas run the same revision.

## Migration Plan

No data or configuration migration. Verify route-level regressions, review independently, then use the authorized HA rollout. Rollback remains an explicit operator action.
