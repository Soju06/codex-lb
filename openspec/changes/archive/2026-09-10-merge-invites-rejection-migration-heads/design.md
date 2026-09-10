## Context

Published PR head a44a6f0d482a4194c7739c77e32726351d41ae21 joins through audit `030000`. Pinned main561311ded1d3191cf1ef271d4cd8ea97f8fd17a4 adds `040000` dashboard_user_invites. Preserve the old candidate and hosted evidence independently.

## Goals / Non-Goals

Restore single-head public upgrade with populated data preservation. No new policy or sibling PR dependency, live effect, or historical migration edits.

## Decisions

Append `20260910_220000_merge_invites_rejection_heads` with parents `20260910_200000_merge_dashboard_users_rejection_heads` and `20260909_040000_add_dashboard_user_invites`. Both operations are no-ops. Isolate the preceding `200000` merge downgrade proof at its own revision so a later invite-table downgrade is not confused with merge-only behavior.

## Risks / Trade-offs

Further target changes need assessment before publication; do not add speculative joins. Preserve current invite constraints and credential/session semantics. Database snapshots include invite hash/status/expiry/creator flags and existing application rows/schema.

## Migration Plan

Prove public upgrade failure on the composed populated graph, then one-head upgrade/check from each parent and both stamps on SQLite and PostgreSQL. Prove merge-only downgrade/reupgrade without deleting invites or changing users/grants/probe state. Run affected invite/permission/CSRF/probe controls and independent review before normal publication.
