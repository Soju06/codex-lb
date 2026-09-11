# Verification — 2026-09-10

- 198 backend tests passed: source dispatch/admission regression plus company source adapters and 14 governance route cases.
- 15 frontend tests passed, including editing/clearing/validating the budget.
- Python ruff/ty, frontend TypeScript project build and scoped ESLint passed.
- OpenSpec strict change validation passed.
- Full repository spec validation: 47 passed / 16 failed. `model-source-routing` passed; all failing spec files are outside this change and have no local diff. Full-repository spec validation is therefore not claimed green.
- Fresh SQLite upgrade/check reported no schema drift; downgrade to previous head and re-upgrade passed.
- Built dashboard inspected via Playwright against the independent FastAPI service. Synthetic screenshot: `docs/screenshots/company-governance-preview.png`.
- Independent release: `~/.codex-lb/releases/20260910-company-governance`, own frozen Python environment, built dashboard.
- Independent state: `/tmp/codex-lb-governance-validation`, port 2472. Imported 27 TRAE, 10 Codebase and 1 LLMBox model presets; all source budgets default unlimited.
- Real Codex CLI shell-tool/readback passed for `trae/GPT-6-Astra`, `codebase/kimi-k2.6`, `deepseek-v4-flash-0731`, each returning the exact local marker. All three sources subsequently reported healthy, two successful calls and observed tokens/latency.
- On the independent service, setting a local budget below recorded usage returned `429 model_source_budget_exhausted` without forwarding; cleared afterward.
- Gracefully restarted port 2472 after confirming zero in-flight requests. All three sources retained healthy status, two successes each and the same token counters; source budgets remained unlimited. Previous release and rollback LaunchAgent file were verified present.
- Production 2455 switched on 2026-09-10 after explicit user authorization. Drained to zero at 19:33:18 CST, took final SQLite backup, upgraded schema and started the permanent governance release. Ready at 19:33:38; 27 TRAE, 10 Codebase and 1 LLMBox models enabled at 19:33:39. Original four GPT accounts retained. Client routing remained unchanged. Backup: `~/.codex-lb/backups/company-governance-20260910-193312`.
- First cutover attempt safely cancelled drain before restarting because the script used the wrong bridge-status field name. Corrected to `http_bridge_restart_blocking`; second attempt succeeded.

No claim is made about remaining company quota, strict token accounting where upstream usage is absent, multi-provider failover or proactive inference health checks.

## Production acceptance

The formal 2455 endpoint passed real Codex shell-tool and exact-marker verification with `gpt-5.6-sol`, `trae/GPT-5.6-Sol`, `codebase/kimi-k2.6` and `deepseek-v4-flash-0731`. All 38 company model rows are enabled; budgets remain unlimited by default. Source cards report healthy with two successful requests each. Request logs confirm subscription requests have account ownership and company requests have source ownership without an account ID.

The first production TRAE Astra probe did not return during approximately three minutes of observation and was cancelled; it is not recorded as a successful production smoke test. TRAE Sol then completed the same test promptly. Astra had passed the independent release test before cutover; current production latency for Astra remains unverified. Cancellation did not increment source failures.

Production config: LaunchAgent `local.codex-lb` runs `~/.codex-lb/releases/20260910-company-governance/.venv/bin/codex-lb` on 2455 with the existing production data directory. User client configuration was not edited. Cutover backup contains the original LaunchAgent, encryption key and final pre-migration SQLite snapshot; the previous independent company-models release is available for rollback.
