# Verification: simplify-key-dashboard-add-install

Verified locally on 2026-09-06. No commit, push, bootstrap, or deployment performed.

## Completeness and correctness

| Requirement | Implementation and evidence |
| --- | --- |
| Default Overview, model-only policies, fixed local lifecycle dates | `key-dashboard-page.tsx`, `key-profile-card.tsx`; integration assertions cover default tab, keyboard navigation, allowed/enforced/unrestricted models, missing dates, and fixed timestamps despite ISO administrator preferences. |
| Preserve usage, safe recent logs, refresh and pagination | Existing key-dashboard route/integration tests remain green; Overview contains the original cards and grid. |
| Authenticated platform installers | `key_dashboard/api.py`, `install.py`; API tests cover all three platforms, missing/invalid/inactive/expired credentials, own-key-only exports, origin-derived endpoints, policy-selected models, unsupported platform rejection and no-store headers. |
| Copy script, download file, curl command with current key | `key-install-panel.tsx`, `install.ts`; frontend tests verify clipboard and file contents for each platform, masked DOM previews, omitted cookies, no-store requests and no key in URLs. |
| Private recoverable setup files | Isolated Bash execution tests verify config/auth contents, Unix 600/700 permissions, repeated unique backups, CODEX_HOME/default-home behavior, injection-resistant serialization, symlink/non-file refusal and backup-failure safety. Windows content tests verify literal data, UTF-8 encoding and private ACL instructions. |
| Failure and stale-response handling | Frontend tests cover script errors/retry, independent 401 handling, platform switches, delayed responses, disconnect/reconnect and use of the new credential. Curl tests prove failed/partial downloads are not executed and temporary files are cleaned up. |

## Validation results

- Backend: **33 passed** with `.venv/bin/python -m pytest tests/unit/test_key_dashboard_install.py tests/integration/test_key_dashboard_api.py tests/integration/test_usage_api.py tests/unit/test_dashboard_auth_dependencies.py -q --disable-warnings`.
- Frontend: **17 passed** across `key-dashboard-flow.test.tsx`, `install.test.ts`, `auth-flow.test.tsx`, and `clipboard.test.ts` using the installed Vitest runner.
- TypeScript project build and Vite production build passed; targeted ESLint, Ruff check/format, and `ty check app/modules/key_dashboard` passed.
- Installed Codex CLI **0.153.4** recognized API-key login after running the generated Bash script against a synthetic key in an isolated temporary Codex home (`codex login status`, exit 0). No real credential or user configuration was used, and no inference request was sent.
- Change and owning `api-key-dashboard` main spec passed strict OpenSpec validation after sync.
- `git diff --check` passed.

## Visual evidence

Playwright captures use synthetic data and a synthetic credential, UTC browser timezone, and the local development server. Desktop viewport: 1440px; mobile viewport: 390px. Install had no page-level horizontal overflow.

- [Overview before](screenshots/overview-before.png)
- [Overview after](screenshots/overview-after.png)
- [Install desktop](screenshots/install-desktop.png)
- [Install mobile](screenshots/install-mobile.png)

## Coherence and limitations

No change-specific critical findings. The implementation follows the scoped design: one authenticated text endpoint, one script generator, shared configuration for clients, explicit credential export, and no new runtime dependency, global setting, admin navigation item, or schema migration.

- Native Windows/PowerShell and macOS App execution were not available on this Linux host. Windows script serialization, ordering and ACL instructions are tested, but native runtime/client smoke testing remains a release-validation limitation. The macOS Bash template was executed using Linux Bash.
- Repository-wide `openspec validate --specs` reports an existing missing Purpose section in `model-source-routing` (58/59 pass). Strict mode reports 23 existing invalid specs (36/59 pass), largely placeholder Purpose sections. Those spec files are unchanged by this task; only `api-key-dashboard` is modified and passes strict validation. Do not interpret scoped validation as whole-repository readiness.
