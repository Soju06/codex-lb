# Account usage limits

## Purpose and model

Operators can reserve quota for direct use by limiting how much Codex LB consumes from an account. This combines the scalar policy from PR #1528 with the independent windows and reserve presentation proposed in PR #2147.

One enabled flag controls a default maximum-used percentage and optional 5-hour and weekly overrides. An absent override inherits the default; without a default, unmatched windows remain unrestricted. Disabling retains saved values, while removal clears them. Existing scalar policies retain their behavior without a conversion to the lowest window threshold.

With 54% consumed and an 80% cap, the provider has 46 percentage points remaining: 20 reserved and 26 usable. With primary/weekly usage of 65%/75% and caps of 70%/90%, both windows remain available; primary usage of 72% blocks the account. The editor displays reserve percentages, while the API stores maximum-used percentages.

## Observation and ownership boundaries

The shared evaluator normalizes weekly-only and monthly-only observations. Window overrides match duration, so monthly and other nonstandard windows use the default. Applicable missing, stale, or elapsed observations fail closed until a fresh measurement arrives. A reset deadline alone does not prove a new zero measurement. Unknown placeholders remain current-state evidence but are excluded from numeric history and demand calculations.

The policy is a hard eligibility gate, including for additional-quota requests. It does not change persisted upstream status. Continuity owners stay pinned: bridge/WebSocket dispatch and every warmup surface authorize the selected account from committed database state. A policy change after an authorization read is not retroactive; already dispatched requests retain ownership and settlement paths. The design document records cache, timeout, retry, and cleanup boundaries.

## Migration topology

The integrated scalar migration retains its original `20260816_000000_add_model_source_embeddings` parent; the override migration follows the scalar migration. Explicit merge revisions join upstream and override histories. The integration change verifies databases built with the previously published scalar parent as well as the retained local lineage. Existing accounts start disabled. Override downgrade disables policies with no scalar threshold before removing the override columns. SQLite and PostgreSQL upgrade/downgrade coverage checks the resulting constraints and data.

## Published PR audit

The audit starts at GitHub PR #1528 head `242a0f937`, against base `ec994599`: 12,390 additions, 785 deletions, 135 files. Initial work stayed local on `fix/pr1528-usage-audit` in a separate worktree, preserving the earlier scalar-only checkout. Verification uses this head's frozen dependency locks.

The audit fixes stale toggles overwriting newer thresholds, unhandled mutation rejection, lease cleanup after WebSocket expiry, and reserve controls offered for incorrect durations. It consolidates telemetry writes, warmup rejection handling, and retry tests. Duplicate assertions remain covered at the public paths. PostgreSQL reconciliation tests wait for the identity read to finish before releasing the writer; transient-stream tests fail whichever account is selected first, avoiding random selection assumptions.

A local component preview compares the published and reviewed controls for 60-minute and 1440-minute windows. It uses synthetic accounts and makes no provider requests. The feature change remains active while the PR is being reviewed locally.

## Audit verification and size

Local checks used the published head's frozen dependencies. The affected unit suite passed 3,801 tests with three obsolete locking scenarios skipped; the final duplicate removal passed its focused 310-test suite. The wider frontend slice passed 556 tests, followed by 21 focused tests after removing its duplicate case. WebSocket, cancellation, and demultiplexing checks passed 229 tests. The bridge/telemetry/proxy/warmup integration slice passed 430 tests with 15 backend-specific skips; its one flaky retry test was corrected, then all 57 retry tests passed. PostgreSQL checks passed eight migration tests, 59 authorization/telemetry tests (five SQLite-specific skips), and nine selected policy/SQL-interruption tests. The corrected PostgreSQL reconciliation test passed three additional runs.

Repository lint, architecture ratchets, type checks, frontend lint/type checks/build, strict change validation, and all 66 main-spec validations passed. Migration topology also passed against the actual PR base, with exactly two added revisions. GitHub's head and base matched the recorded baseline before publication. The seven audit commits were subsequently published at `f51a53758` for cloud CI and a full CodeRabbit review.

CodeRabbit found an override sample check treating unknown plan capacity as zero. Unknown capacity now requires a matching current sample, while known zero capacity remains exempt. Expanded existing tests reproduced the selection bypass; all 505 focused evaluator, selection, and owner-authorization tests then passed, including bridge admission. Lint, types, and strict change and main-spec validation also passed.

The next full review identified the same required-window gap in the default frontend mock and unnecessary legacy-bootstrap flags in the override round trip. The mock now requires applicable override windows before filtering observations, retaining zero-capacity and monthly-only exceptions; 41 focused frontend tests and frontend lint/types passed. Existing-schema upgrades use `bootstrap_legacy=False`; four SQLite checks and both PostgreSQL policy round trips passed. Both PostgreSQL round trips are now included in the required CI test target.

The reconnect regression now bounds its worker-thread waits and releases both events before WebSocket/TestClient teardown. It checks each expected response immediately so an authorization failure cannot leave it waiting for a second success event. Sixteen related WebSocket cases passed, followed by the final edited reconnect case; lint and types also passed.

Both size columns below compare the entire PR against `ec994599`; tests include frontend mock support. Added/deleted are raw Git diff counts, while net is added minus deleted.

| Entire PR | Published `242a0f937` | Reviewed `f51a53758` |
| --- | ---: | ---: |
| Added | 12,390 | 12,194 |
| Deleted | 785 | 836 |
| Net growth | 11,605 | 11,358 |
| Changed files | 135 | 135 |

Net growth by category: production: +2,672 to +2,648; tests: +8,006 to +7,802; docs: +925 to +906; other: +2 to +2.
