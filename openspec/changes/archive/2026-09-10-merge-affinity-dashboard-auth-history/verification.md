# Verification

Target 8e5760726a34332d869aac682a3932170621966b; prior published candidate b5350ed8226cac677b017afa142b554c74438dce. Both-parent populated public upgrade failed with MultipleHeads before convergence. The new merge joins affinity180000 and audit030000. All 242 prior-candidate migration files and all 244 target migration files remain byte-for-byte unchanged.

SQLite lifecycle: 8 passed. PostgreSQL18.6: 5 passed, including both populated histories, merge-only downgrade/reupgrade, ledgerless bootstrap and existing fresh-upgrade/drift controls. Final fixture compatibility adjustment preserves the canonical conftest database URL; its 3 SQLite tests passed again. The new module is explicitly selected by the hosted PostgreSQL target. Exact disposable PostgreSQL container and volume were removed.

Current-target public controls: 63 passed across stored-user metadata permissions, password login/session revocation, guest generations, CSRF, sensitive permission gates and audit attribution. Earlier 78-control result on the identity-only target remains separate historical evidence.

Public migration CLI upgrade and check pass with no drift. Lint/typecheck, strict documentation build and 65 strict specs pass. Independent Medium standards and spec reviews of tree97e777a2b8695be6b7a53d68e8fa32930065ae8b found no actionable findings. Only verification prose/task completion and archive location changed afterward.

Ledgerless bootstrap follows the released legacy credential projection. Ledgered merge-only reupgrade preserves newly rotated user credentials and session generations without replaying that projection. No authentication policy, routing behavior or live environment was changed. Current-head hosted CI/review remains a separate delivery gate owned by the PR worker until explicitly transferred.
