# Design

Join 20260910_200000_merge_affinity_identity_heads and 20260909_040000_add_dashboard_user_invites with new220000 merge. Neither parent is modified. Test populated upgrades from both parents, then merge-only downgrade/reupgrade preserving both version rows and all data. Invite hashes, timestamps, creator snapshots and flags remain identical; upgrading the older branch creates an empty invite table. Retain the already-verified role/user/credential/audit preservation tests and protected metadata HTTP controls. Add invite/role management controls that could affect permissions or session validity.

Only dedicated disposable databases are used, with identical main/test database variables before imports. The PR worker retains hosted CI/review ownership until accepted transfer. No live migration or policy expansion.
