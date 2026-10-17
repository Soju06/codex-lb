# Authorized hotfix rollout — 2026-10-02

The operator explicitly approved publishing the focused patch, isolated rehearsal, and the documented rollout/rollback plan. Upstream merging and archiving remain separate maintainer decisions.

## Published source

- PR: https://github.com/Soju06/codex-lb/pull/2561
- Branch: `mustafa0x:fix/http-bridge-reacquire-caps`
- Implementation: `3430707595c5c62ea947b9daff79faa6aaf6c613`
- Exact-head CodeRabbit review covered all 13 changed files and found no actionable comments; zero review threads. Review run `c89418fe-9891-4404-b329-91b171b2668c`.
- Cloud run `37056334712`: test suites, PostgreSQL tests/migrations, lint, typing, OpenSpec, package, and browser smoke passed. **Docker build / CI Required failed** because Trivy reports two HIGH advisories in unchanged `urllib3 2.7.0` (`CVE-2026-97687`, `CVE-2026-97689`, fixed in 2.8.0). This is a real outstanding dependency security gate, not a flaky network check. It has not been bypassed or described as green; the PR remains draft and unmerged.

This operational hotfix retains the deployed dependencies byte-for-byte and adds no new dependency exposure. It corrects an incident on the existing base; it is not a dependency remediation release. The separately scoped security update remains necessary before an upstream merge/release can claim all gates are green.

## Immutable image rehearsal

- Original image ID: `sha256:496fd93c85ae90d18dbf4c085a005e84035e18a021886b6e6f944ab0a7b4ff67`.
- Candidate: `codex-lb:bridge-caps-343070759`, ID `sha256:7af731eb2189a8557ee74ccac7e0676bd534b6e0e488e2e2836fc1c6b41e83f9`.
- Platform/interpreter: Linux ARM64 / Python 3.14.7, matching deployment.
- Candidate inherits the verified original image; no pull or dependency installation during build. A file-hash comparison across all **5,860** application/config/scripts/venv files found exactly one changed file: `app/modules/proxy/_service/http_bridge/request_submit.py`.
- Fixed source SHA-256: `2ab0bb6ee1c5a7b494609a0f311993aa2f510aa308d67535f73688868f8107fd`.

Rehearsal containers had no network, no published ports, no production environment or credentials, no production volume or ring, a read-only root filesystem, disposable `/tmp`, read-only tests/tooling, and bounded CPU/memory/PIDs. They imported application modules from the candidate image, not a mounted source checkout. Test tooling was outside the image; the deployed virtualenv was unchanged.

Executed image checks:

- Candidate idle-lease/new matrices/balancer concurrency/public routes: **215 passed**.
- Candidate entire HTTP bridge integration suite: **196 passed**, no deselection, with `--timeout=120`.
- Original image negative control: all **8** targeted higher/unlimited-cap cases failed with `account_stream_cap` as expected; the other eight matrix cases were explicitly deselected for that negative control.
- Unchanged reader-handoff test: **passed on both images**, 62.98 / 63.07 seconds. The earlier short runner deadlines were below the real 60-second watchdog; the assertions and application timeouts were not changed.
- Main plus patch full HTTP bridge/proxy-utils rerun with adequate runner timeout: **1,611 passed**.

Rehearsal setup notes: exporting the base image to the local Docker host exceeded the transfer deadline, so the candidate was built on the production host without touching its service. A first test-tools packaging attempt rejected a macOS-native optional test dependency; only portable pytest tooling needed for the chosen image suites was shipped. A first isolated import correctly failed because the read-only default key location was unwritable; its newly generated disposable test encryption key was then explicitly located under `/tmp`. Neither correction altered the candidate or used production data.

## Cutover and acceptance

**Final handoff state: original image restored; the hotfix is staged but not retained in production.** The approved runner preserved the old image/compose, verified consistent SQLite backups, used reversible operator drain, and refused to stop with a nonzero in-flight count. No database was restored or schema changed, and no cap was raised.

Independent read-only operational review session `d195a03f-2445-4a60-ba88-ef2a2a6c533e` found four actionable script issues, all corrected before execution: operational safety checks cannot depend on removable Python assertions; smoke must verify upstream rather than downstream transport; privileged helper files need hash/ownership/mode checks; and failed drain-stop must be retried/confirmed without masking the original error. Re-review found no actionable findings. Subsequent smoke-correlation and read-only quiet-window changes were also reviewed in that session with no actionable findings.

### Attempt 1: candidate smoke succeeds, conservative automatic rollback

- Deployment stamp `20261002T201037Z`; verified 1,756,774,400-byte SQLite backup with `quick_check=ok` and unchanged revision `20260913_000000_add_oidc_provider_flow`.
- Drain reached zero in-flight requests and no blocking bridge work before stopping. Candidate started at **20:11:20 UTC**; database and single-member ring readiness passed, with the expected source hash and unchanged schema.
- A new ten-minute-expiry, model-restricted, token-limited smoke key existed only in process memory. Four short real requests completed: `/v1/responses` first/follow-up in **2.047 / 1.814 seconds**, and `/backend-api/codex/responses` first/follow-up in **4.006 / 1.737 seconds**.
- All four usage reservations finalized. However, the acceptance script initially queried terminal logs by the client HTTP request IDs. This deployment stores the upstream `response.completed.response.id` in terminal bridge `RequestLog.request_id`, so the query incorrectly found zero rows and triggered automatic image rollback.
- Read-only inspection confirmed all **four key-attributed success logs**, downstream `http`, upstream `websocket`, and the disabled smoke key. The original image was restored at **20:12:38 UTC** with healthy readiness and admission open; no accounting data was rolled back.
- The corrected script records the upstream response IDs and constrains the log lookup by smoke key ID, retaining all four completion, attribution, websocket, and settlement requirements. No application or candidate-image change was needed.

### Attempts 2 and 3: safely aborted before stopping

- `20261002T201424Z`: the 25-second reversible drain gate still showed **three** in-flight connections and one queued/pending HTTP bridge request. Drain was stopped and admission reopening confirmed; no container restart occurred.
- `20261002T201714Z`: after an additional read-only wait for a quieter period, the strict drain gate still showed **one** in-flight connection. HTTP bridge work was clear, but that is not proof the remaining connection was idle or safe to interrupt. This attempt also aborted before stop and confirmed drain was off.
- No further automatic retries or forced disconnections were performed. Completing the rollout requires an operator-approved maintenance window that permits a bounded graceful restart of any remaining long-lived connections, or a genuinely empty drain window. Tasks 4.3 and 4.4 remain incomplete.

### Handoff evidence, 20:22 UTC

- Production image remains the original `sha256:496fd93c85ae90d18dbf4c085a005e84035e18a021886b6e6f944ab0a7b4ff67`; original source hash is present. Compose bytes match the preserved original (SHA-256 `bd0a6c4c304fea0e883954ba580606098e9dc17309a51525f7c49953ef2be36e`).
- Drain and bridge-drain flags are false. Database readiness passes with one healthy ring member; public `/health` and `/health/ready` return 200. Current container has zero automatic restarts and no OOM; the explicit candidate/rollback recreations above must not be conflated with that counter.
- The bounded completed-request sample since the first attempt contains **145 successes / one client-disconnected cancellation**, no recorded terminal errors. This covers both candidate and restored-original periods, excludes failures before logging and ongoing stalls, and does not prove the warm-cap defect is fixed in the currently running original image.
- The reviewed candidate remains available by its immutable ID. Host disk has approximately 79 GiB free after preserving rollback assets. No rehearsal container or rollout controller remains running.

## Retained rollback and retry assets

Private deployment directory: `/srv/apps/codex-lb/hotfix-bridge-caps-343070759/`. Each attempt's timestamp subdirectory contains the old image archive, old/candidate compose files, root-owned mode-600 verified database backup, metadata, and terminal result. Database backup SHA-256 values:

| Stamp | SHA-256 |
| --- | --- |
| `20261002T201037Z` | `aa8aff83363542f11415dd25268c62ec34fc98c07c7cb7a7b1cfcc9b862b26e0` |
| `20261002T201424Z` | `e889ce3f1e5d57da81b8365dcb0eb01f009cb3022ec42476bd5edf05ccd134ca` |
| `20261002T201714Z` | `0585e8a2c5f060fc942c5a336de73c384f3a44698b91333dcfbe2246a0878509` |

Reviewed operator helpers are retained root-owned/read-only, with their earlier versions alongside the attempt logs. Final controller SHA-256 is `835a7f64cb72ef96204173398a9bf73fb5d03c58c488f95edb385a16714619c2`; backup helper `41d14134f0e05de16b59833181b04fa1e72b68f7ac5c27e71bc8244a64ac459e`; corrected smoke helper `2430dff6aa87184c42899e304caea2fb6b8178fd017f712c8803cc64f6c86942`.

For any later authorized retry, recheck the running image, compose, candidate hash, account capacity, and cloud findings rather than assuming this handoff is current. Preserve a fresh consistent backup, agree the interruption policy, and rerun corrected smoke plus an observation window. Roll back only application/compose on failure, not the database: new request/accounting writes must survive. The disabled smoke-key row is retained for audit; its credential was never written to logs or artifacts.
