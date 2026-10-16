## Context

New OAuth/import accounts currently initialize their per-account warm-up flag to false. Dashboard settings already persist warm-up policy and propagate updates through the shared settings cache.

## Goals / Non-Goals

**Goals:** Let operators choose the warm-up default for future accounts in the existing settings panel; preserve prior account choices.

**Non-Goals:** Bulk-edit accounts, create another scheduler, change quota thresholds, or send traffic at login.

## Decisions

- Add one non-null boolean `limit_warmup_auto_enable_new_accounts` to dashboard settings, default/server default false. Enrollment is opt-in; global warm-up still defaults off.
- Read the existing settings cache at OAuth token persistence and import time. Settings writes retain existing cache invalidation and version checks. Read at completion, not OAuth start.
- Keep credential replacement behavior unchanged, so neither an enabled nor a disabled enrollment default overwrites an existing account's stored flag.
- Place an immediately saved switch beside global warm-up, with copy explaining that it only affects new OAuth/import accounts. Keep it editable while global warm-up is off.
- Extend API response/update schemas, frontend parsing and update payload, translations, and settings audit tracking together.

## Risks / Trade-offs

- Older application replicas do not understand the setting during a rollout; full behavior starts after rollout completes.
- A setting change racing a login follows the settings snapshot read at token persistence; later setting changes do not rewrite that account.
- Migration adds a default for existing settings rows but never touches account flags. Upgrade/downgrade coverage verifies this and the single-head graph.

## Migration Plan

Add the column after `20260918_000000_merge_scim_and_overflow_heads`. Existing settings rows receive false. Downgrade drops only the new column.
