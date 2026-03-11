## Context

The proxy now persists additional usage snapshots by canonical upstream `limit_name`, but model routing still needs a deterministic bridge from requested model IDs to those persisted rows. The fix touches proxy selection, error reporting, and the Accounts-page label formatter, so the design needs one shared mapping and guardrails that preserve existing balancer/runtime semantics.

## Goals / Non-Goals

**Goals:**
- Resolve explicitly gated models through one canonical `model -> limit_name` mapping.
- Filter candidates using persisted additional-usage freshness before normal selection state is built.
- Preserve existing `AccountState` and persisted account-status semantics while checking additional quota eligibility.
- Surface a user-facing mapped label for known gated limits.

**Non-Goals:**
- Redesign the general model registry or account-plan routing system.
- Change upstream additional usage payload formats.
- Broaden UI copy changes beyond mapped additional quota labels.

## Decisions

- Introduce a small shared registry/helper for explicitly gated models that returns both canonical `limit_name` keys and display labels. This avoids duplicating ad-hoc maps across proxy and frontend code.
- Treat persisted `additional_usage_history` as the eligibility source of truth for mapped models. Selection fails closed when the snapshot is stale or missing instead of silently falling back to unrelated accounts.
- Keep eligibility filtering separate from mutable balancer runtime transitions. Candidate pruning may inspect status/runtime snapshots, but it must not rewrite persisted account status or alter the meaning of `AccountState` fields.
- Return stable proxy error codes for the three gated-model failure modes so callers and logs can distinguish plan mismatch, stale quota data, and zero eligible accounts.

## Risks / Trade-offs

- [Freshness window is too strict] → Use a bounded freshness rule tied to the refresh interval and cover it with tests.
- [Shared-file churn in proxy selection code] → Keep the diff small and separate pure mapping helpers/tests from selection-path changes.
- [UI and backend mappings drift] → Reuse one canonical registry shape and add regression tests for seeded models.

## Migration Plan

- Ship the shared mapping and selection-path changes together so mapped models never point at unmapped quota keys.
- No data migration is required because `additional_usage_history.limit_name` remains the persisted source of truth.
- Rollback is safe by removing the gating map and reverting to current routing behavior.

## Open Questions

- None for the initial seed model set; new mapped models can be added as follow-up registry entries.
