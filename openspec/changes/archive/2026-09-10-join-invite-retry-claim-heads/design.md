## Decision and proof

Use an explicit no-op join with parents20260910_200000_merge_users_retry_claim_heads and20260909_040000_add_dashboard_user_invites. Preserve published200000 and every earlier revision. Reproduce the public upgrade failure from populated parents; verify upgrade/check and exact schema/data preservation across named-parent join-only downgrade/re-upgrade.

Retain historical merge scenarios. Share the existing CLI/snapshot/seed test fixtures if required to keep each test file focused as the new scenario is added. Add bounded invite/account/permission controls and meaningful hosted PostgreSQL selection, retaining unchanged evidence from earlier repairs.

## Limits

The join does not choose invite policy, permission delegation, receipt lifetime, settlement or activation rules. Those remain the incoming implementation and existing unresolved decisions. No sibling graph dependency or live operation is introduced.
