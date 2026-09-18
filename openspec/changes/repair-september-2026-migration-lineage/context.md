# Context

The SCIM migration adds `dashboard_scim_tokens` and
`dashboard_identities.user_name`. The overflow-retirement migration removes
the withdrawn router's table and settings columns. These changes are
independent, but both revisions name
`20260913_000000_add_oidc_provider_flow` as their parent.

The overflow-retirement change landed later and has not shipped in a release.
The repository's topology guard requires a fresh timestamp and a linear parent
for that case. The repair therefore leaves the SCIM revision unchanged,
restamps the later revision, and makes SCIM its parent.

The failure mode is source-level. No production database stamp or row needs
manual repair. Normal Alembic upgrade walks the repaired linear graph.
