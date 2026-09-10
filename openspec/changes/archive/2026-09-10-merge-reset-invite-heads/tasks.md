- [x] Reproduce populated public upgrade-head failure from both parents.
- [x] Append the merge and preserve both sets of route/dependency registrations.
- [x] Verify complete invitation, user, role/grant, generation and reset rows through upgrade, merge-only downgrade and re-upgrade with no drift.
- [x] Run relevant invitation/role/session, reset authorization and CSRF controls; verify PostgreSQL selection and local PostgreSQL composition.
- [x] Complete static/spec checks and independent Medium review before normal publication.

Current-head hosted CI and actual review remain separate publication gates.
