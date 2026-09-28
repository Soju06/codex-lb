## Verification

Verified on 2026-09-28. Both added requirements and all four scenarios are implemented with no unresolved correctness or design findings.

| Contract | Implementation and evidence |
| --- | --- |
| Preserve HTTPS behind HTTP ingress | Export endpoint accepts only optional `scheme=https`, preserves authority/port and leaves absent-hint behavior unchanged. API integration tests cover all platforms and rejected values. |
| Include browser protocol in every export path | Shared frontend path builder serves both script requests and terminal commands. Dashboard integration tests run over HTTP and HTTPS and cover script copying, download and terminal copying. |
| Identify catalog clients | Python and PowerShell use `codex-lb-installer/1.0`; generated installers run against a local server that rejects other signatures. The authenticated export-to-execution integration preserves scoped alias metadata. |
| Preserve failure safety | Forbidden downloads report HTTPS/proxy/firewall guidance without credentials or server bodies. Existing redirect, backup and lifecycle coverage passes. No global proxy trust or production state changes. |

## Checks

- Focused Python run: `tests/unit/test_key_dashboard_install.py`, `tests/integration/test_key_dashboard_api.py`, and `tests/integration/test_key_dashboard_install_catalog.py`, excluding the unchanged Codex chat-resume smoke: 62 passed, 17 initially skipped because PowerShell was absent from PATH.
- Reused the existing `/tmp/codex-lb-pwsh` PowerShell 7.4.6 runtime to exercise the skipped catalog/lifecycle checks. All 18 selected PowerShell/Windows cases passed after correcting one terminal-line-wrapping assertion and rerunning the affected case. This covers 79 distinct Python tests across both runs.
- Frontend installer-command and key-dashboard integration suites: 20 passed.
- Focused Ruff lint and formatting, frontend ESLint and TypeScript build-mode type checking: passed.
- Change strict validation and all 66 main specs under `openspec validate --specs --strict`: passed after synchronization.
- Live read-only catalog smoke: a newly generated macOS installer using HTTPS completed successfully against the reported endpoint in a private temporary Codex directory; generated configuration selected the HTTPS endpoint and the catalog file existed. Temporary output was removed.

## Limits and rollout

PowerShell execution used Linux; native Windows ACL behavior was not reverified and is unchanged. No production deployment was performed. The separately authorized rollout must publish backend and frontend changes, after which the user refreshes Install and re-exports their command/script. Existing saved scripts retain the old embedded transport behavior.

Stable rationale, an HTTPS export example, edge-filter failure modes and re-export guidance are synchronized into the main capability context. The implementation preserves the design's installer-only scope and adds no dependency or configuration setting.
