- [x] Reproduce populated public upgrade-head failure for both current graph parents.
- [x] Append a merge revision without changing published migration blobs.
- [x] Verify exact roles, grants, users, identities, settings and reset data through upgrade, merge-only downgrade and re-upgrade with no drift.
- [x] Verify public reset settings authorization, same-origin success and cross-site rejection before side effects, plus affected main auth controls.
- [x] Verify hosted PostgreSQL selection, static/spec checks and independent Medium affected review.

Current-head hosted CI and actual review coverage remain a separate publication gate.
