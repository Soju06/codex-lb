# Verification: Japanese SCIM locale synchronization

Addresses [PR #2118 comment 5866459612](https://github.com/Soju06/codex-lb/pull/2118#issuecomment-5866459612).
Verified on 2026-09-28 after merging main `4dcf8f751b9b8ee8b55012eda7d459832f3ec51c` into the existing PR branch at `0cf4b29620e6051d4c2a8c0658a64042dfc2c6b4`.

## Reproduction and repair

The unmodified merge reproduced the two reported failures in `src/i18n/index.test.ts`: Japanese key parity and the interpolation/markup check. English had 2,030 keys and Japanese had 2,007, with exactly 23 missing keys and no obsolete keys.

The repair adds the 20 `organisation.automaticAccounts.*` entries, `organisation.errors.scim_token_not_found`, and both automatic-management summaries. All four bundles now contain the same 2,030 keys. Existing Japanese values are unchanged; the new values preserve both `{{label}}` occurrences and `{{when}}`. Independent semantic review found no issues with the prerequisites, account-matching guidance, credential replacement/removal, or one-time secret explanation.

## Validation

Frontend commands run from `frontend/`; OpenSpec and Git commands run from the repository root.

| Check | Result |
| --- | --- |
| `bun install --frozen-lockfile` | Passed against the merged lockfile. |
| `bun run test src/i18n` before repair | 2 failed, 11 passed; reported failures reproduced. |
| `bun run test src/i18n` after repair | 13 passed. |
| Reports and API-key edit regression tests | 2 files / 50 tests passed. |
| `bun run lint` | Passed via `make ci`. |
| `bun run typecheck` | Passed via `make ci`. |
| `bun run test:coverage` | 181 files / 1,663 tests passed in 303.20 seconds; all coverage thresholds passed. |
| `bun run build` | Passed, including TypeScript checks; the existing large-chunk advisory remains. |
| Python architecture, cancellation, timing, settings-tier, and migration-topology checks | Passed via `make ci`; 263 migrations, one head, no revisions added relative to main. |
| `ruff check`, `ruff format --check`, and `ty check` | Passed via `make ci`. |
| `openspec validate sync-japanese-scim-locale --strict` | Passed using the CI-pinned OpenSpec 1.11.0. |
| `openspec validate --specs --strict` | 66 passed. |
| `git diff origin/main --check` | Passed. |

The repository-wide `make ci` gate completed the frontend and Python static checks, then stopped because `cargo` is not installed locally. Rust, backend test suites, packaging, Docker, and Helm stages were therefore not run locally. The frontend coverage was 84.14% statements, 77.67% branches, 79.88% functions, and 84.51% lines.

The two older Codex threads were checked against the existing implementation and regression tests: Reports uses the shared date formatter and preferences, and API-key edit usage labels use the existing locale keys. Both review threads were resolved as requested.

## Scope and verification assessment

The new scenario maps to the existing organisation summary and automatic-account-management translation call sites. The locale test is the regression gate for the reported failure; existing organisation tests cover prerequisites, account matching, credential lifecycle, summary disclosure, and loading errors. No new application behavior or tests are introduced by this translation repair.

The frontend requirement and stable context include the SCIM coverage. A new browser/screenshot run is omitted under the maintainer's explicit waiver for this synchronization.

Completeness, correctness, and coherence checks found no remaining issues within this translation sync. All requested keys are present, the modified requirement maps to existing translation call sites, and the implementation follows the established locale bundle pattern.
