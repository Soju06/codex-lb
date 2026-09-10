# Verification

Reviewed code `09478f96fb56d78f80d5036a7ec6d0c0e2afacc6`, tree d19de9e34afae89b9afef0eb53447186dcb46680, target `561311ded1d3191cf1ef271d4cd8ea97f8fd17a4`.

- Populated public CLI reproduces multiple heads before the new join. Regression tests fail before the revision.
- Focused new/historical migration suites pass 11SQLite and 11PostgreSQL18.6 tests. Pending, consumed, revoked and expired invites, credential hashes, creator snapshots, flags, roles/users/generations/ownership/audit/rejection rows and schemas survive both-parent upgrade and merge-only roundtrips.
- SQLite invite/permission/CSRF/probe/delegation/redaction controls: 82 pass; one obsolete head-equality assertion failed, was changed to single-head ancestry, and passed its targeted rerun. PostgreSQL invite/permission/CSRF/probe controls: 71 pass.
- All 493 published revision/path blob checks match unchanged. Lint, format, architecture, type checking, release guards and strict change validation pass. The final test-only assertion change passes targeted lint/format/type checking.
- Independent Medium Input and Standards reviews report no actionable issue on the pinned code. Final archive changes documentation only. Earlier unaffected migration/policy evidence remains applicable.

Both database variables are identical dedicated disposable URLs before imports; main/background/test engines match. No live, merge, closure or force action. Hosted validation and actual current-head review remain required after normal publication.
