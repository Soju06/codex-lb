# Design

The published affinity history ends at 20260910_180000_merge_affinity_guest_heads. Main ends at 20260909_030000_add_audit_actor_columns, through the role, user and credential projection revisions. Append a merge with these two parents; upgrade and downgrade perform no schema or data operations. Reuse the public migration entry point and request-log HTTP contract.

A database on the affinity history must execute the existing role/user/credential migrations exactly once. A database already on the authentication history must retain its roles, grants, users, identities, credentials, session generations and audit actor data while adding nullable affinity columns. Merge-only downgrade returns both version rows without rolling back either branch. Reupgrade must not repeat the credential projection or overwrite newer user credentials.

All data is synthetic and both database environment variables point to the same disposable database before imports. No live migration or policy change is authorized. Existing b5350 source/hosted proof remains an immutable prior result.
