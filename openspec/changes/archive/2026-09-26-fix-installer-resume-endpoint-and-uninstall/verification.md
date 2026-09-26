# Verification

| Dimension | Result |
| --- | --- |
| Completeness | 6/6 tasks, 2/2 requirements implemented |
| Correctness | Real CLI resume, exported Bash/PowerShell lifecycle, and UI coverage passed |
| Coherence | Root override, offline restore, stable baseline and untouched sessions follow the design |

## Evidence

- `tests/unit/test_key_dashboard_install.py`, `tests/integration/test_key_dashboard_install_catalog.py`, `tests/integration/test_key_dashboard_api.py`: **68 passed** with PowerShell 7.4.6 on PATH.
- Codex CLI **0.157.1**: create a persisted built-in-provider chat using the old endpoint/key; execute the exported installer; resume with both the current provider and explicit built-in provider; verify new endpoint/key and earlier prompt in the request; uninstall and resume using the original endpoint/key. Inherited endpoint/key variables remain stale throughout. All inference uses a local HTTP fixture, and project config/session files are preserved.
- Exported lifecycle regression cases cover repeated installs, original-file absence, pre-existing uninstall scripts, unchanged baseline, post-install edits, offline/repeated uninstall, invalid state, traversal, missing/corrupt/unexpected backup files, symlinks and backup failure. Targets remain unchanged on validation/backup failure.
- Frontend key dashboard and command suites: **16 passed**, including copying credential-free uninstall commands for all platforms and resetting copied state when the platform changes.
- Scoped Ruff lint/format, Python typing, frontend TypeScript and ESLint, and `git diff --check` passed.
- Change strict validation and the synchronized `api-key-dashboard` spec passed. Repository-wide strict validation remains **50 passed / 15 failed**, with exactly the same failing spec IDs as the previous baseline.
- [Before](screenshots/before.png), [after](screenshots/after.png), and [390px mobile](screenshots/after-mobile.png) captured with mock data. Mobile has no page-level horizontal overflow.
- Unrelated workspace file hashes remain unchanged; locale files retain the existing unrelated warmup translations.

## Limits

Native Windows ACL operations require a Windows host. Linux PowerShell tests replace only the Windows ACL primitives and execute the exported lifecycle logic; they do not establish native Windows ACL correctness. The UI/docs describe explicit profile/CLI overrides and do not promise migration of arbitrary custom-provider sessions.

No blocking findings for this change. The source change has not been committed or deployed.
