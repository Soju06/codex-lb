## Context

Pinned main is 8e5760726a34332d869aac682a3932170621966b; published PR head is 8685fec44bd0d4ee474fc8ab6dda21d086c08d02. Main adds roles, users, compatibility credential re-projection, then audit actor columns after the guest revision.

## Goals / Non-Goals

Restore the public single-head upgrade while retaining published migration blobs, role grants, users, identities, session generations, rejection evidence, audit rows and spool retention. No sibling dependency or auth/ERR policy change.

## Decisions

Join 20260910_180000_merge_guest_rejection_heads and 20260909_030000_add_audit_actor_columns with a new no-op revision. Test the public CLI on each populated parent and both stamps, and Alembic merge-only downgrade to each parent followed by public reupgrade/check. Missing history still performs its specified default/backfill/re-projection; the merge itself changes no rows or schema.

## Risks / Trade-offs

Earlier merge regressions must distinguish historical merge-only downgrade from later destructive schema downgrades. Preserve their original proof and use isolated historical joins for downgrade tests. Verify exact current main before publication.

## Migration Plan

Use normal upgrade head. Existing custom grants, user credentials and session generations at the current main parent remain untouched. A legacy credential on the rejection parent is projected by the existing dashboard-user history. Run dedicated disposable SQLite/PostgreSQL controls and affected permission/CSRF/probe routes.
