## Context and decision

Join20260910_170000_merge_guest_retry_claim_heads with20260909_030000_add_audit_actor_columns. The latter descends from roles000000 and users010000. Use a new explicit no-op revision; never reparent an applied migration. Alembic applies the missing branch before changing version stamps.

## Proof

Reproduce failure through the public CLI from populated parents. Exercise upgrade/check, all preexisting row fields, incoming custom-role grants and user/session state, then direct downgrade to each named immediate parent with exact schema/data preservation and re-upgrade. Retain earlier joins as historical regression coverage. Run affected roles/users/permissions/CSRF contracts and hosted PostgreSQL selection on the combined candidate.

## Limits

The incoming migrations retain their documented credential projection and preset seeding behavior. The join itself changes no schema or data. Removing either branch is outside merge-only downgrade proof. Original receipt and live-activation gates remain open.
