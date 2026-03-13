## Context

The previous fix assumed upstream `limit_name` was a stable canonical identifier and bridged models to that raw field with a local map. Remote verification proved that assumption false: upstream now reports Spark quota rows as `GPT-5.3-Codex-Spark`, while the router still looks up `codex_other`. Usage refresh remains healthy, but selection fails closed because the lookup key and the stored key come from different authorities. The corrected design must separate raw upstream names from one internal canonical `quota_key`.

## Goals / Non-Goals

**Goals:**
- Resolve explicitly gated models through one canonical `model -> quota_key` mapping.
- Normalize upstream alias inputs (`limit_name`, `metered_feature`) to the same canonical `quota_key` during persistence.
- Filter candidates using persisted additional-usage freshness addressed by canonical `quota_key` before normal selection state is built.
- Preserve existing `AccountState` and persisted account-status semantics while checking additional quota eligibility.
- Surface a user-facing mapped label for known canonical quota keys.

**Non-Goals:**
- Redesign the general model registry or account-plan routing system.
- Change upstream additional usage payload formats.
- Remove raw upstream limit metadata from persistence or observability.
- Broaden UI copy changes beyond mapped additional quota metadata.

## Decisions

- Introduce a config-backed canonical additional-quota registry keyed by internal `quota_key`. Each entry owns routed model IDs, recognized upstream aliases, and one display label.
- Treat registry updates as an explicit operational action: supported refresh paths are process restart or explicit registry reload, not implicit file-watch hot reload.
- Add `quota_key` to persisted `additional_usage_history` rows. Keep raw upstream `limit_name` and `metered_feature` columns for debugging and future alias discovery.
- Normalize every incoming additional usage payload row to canonical `quota_key` at ingest time. Unknown aliases fall back to a deterministic normalized raw key so generic additional quota flows still work.
- Treat persisted `additional_usage_history.quota_key` as the eligibility source of truth for mapped models. Selection fails closed when the snapshot is stale or missing instead of silently falling back to unrelated accounts.
- Keep eligibility filtering separate from mutable balancer runtime transitions. Candidate pruning may inspect status/runtime snapshots, but it must not rewrite persisted account status or alter the meaning of `AccountState` fields.
- Return stable proxy error codes for the three gated-model failure modes so callers and logs can distinguish plan mismatch, stale quota data, and zero eligible accounts.
- Expose canonical quota metadata to the Accounts page so labels no longer depend on raw upstream `limit_name` strings.

## Risks / Trade-offs

- [Freshness window is too strict] → Use a bounded freshness rule tied to the refresh interval and cover it with tests.
- [Canonical-key migration leaves old rows unreachable] → Backfill `quota_key` for existing rows during migration and verify both SQLite/PostgreSQL paths in tests.
- [Unknown upstream alias causes silent mis-grouping] → Normalize unknown aliases to a deterministic fallback key and log when they do not match a seeded canonical entry.
- [Future routed quotas still require code edits] → Load the registry from runtime config/data rather than a hardcoded Python table so operators can add aliases/models without source changes.
- [UI and backend mappings drift] → Reuse one canonical registry shape in backend codepaths and expose canonical metadata to the frontend.

## Migration Plan

- Add `quota_key` to `additional_usage_history`, backfill existing rows from raw `limit_name` / `metered_feature`, and add indexes that support canonical-key lookups.
- Ship ingest normalization and selection-path changes together so mapped models never point at unmapped quota keys.
- Preserve raw `limit_name` and `metered_feature` values so rollback/debugging can still inspect upstream payloads.
- Rollback is safe by ignoring `quota_key` and reverting reads to raw `limit_name`, because raw upstream values remain present.

## Open Questions

- None for the initial seed model set; new routed quotas can be added by updating the registry config with extra aliases/models.
