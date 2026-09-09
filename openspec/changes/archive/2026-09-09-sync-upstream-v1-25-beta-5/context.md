## Purpose and scope

Prepare beta.5 locally after the operator accepted the upstream assessment on 2026-09-09. Source is fork `7982375e8a86a5d0c5caf13de0c91458b43c4165`; target is upstream `b54696466d8e073832b37dd0655a14ca776fc00b`. The release is five commits after beta.4. Upstream main at assessment was `d3f63331`, another 40 commits including migrations and configuration changes; beta.6 PR #2202 was still open.

## Preservation decisions

Retain account reauthentication quarantine, native WebSocket shared byte budgets and failure detail, UTC+7 key limits, standalone key dashboard/installers, multi-file import, proxy assignment, control/search behavior and active-active HA deployment. Only native imports and spec/settings documentation were predicted to conflict, but automatic merges still require semantic regression checks.

## Failure example

Account A owns a raw legacy Codex session. An accepted bridge request receives `response.created` and then an output-free overload. Beta.4 excludes A despite reconnecting through the same hard affinity, so selection cannot choose an account. #2150 retains A in selection and preserves the visible response ID. A genuinely soft replacement to B must not send A's upstream turn-state token to B.

## Post-release assessment boundary

Review #2173 (`0ebf03f3`), #2143 (`9703ef9b`) and #2078 (`3d34092d`) using their source diffs and tests. The fork already has an unbounded HTTP transport-event queue and 32-event cooperative reader batches; its WebSocket raw/decoded queues share a 128 MiB connection budget and the configured helper budget (1 GiB in the HA production profile, 256 MiB by default). These are not equivalent to upstream's old 64-event queue. Record residual risks and deferred work rather than claiming the raw upstream patches are drop-in fixes.

## Post-release decision

- #2173: defer the raw patch. Its WebSocket event-count cap and overflow-discard behavior conflict with this fork's shared byte budgets and accepted-prefix preservation. The fork already passes bursts beyond 64 events; its HTTP queue remains unbounded, so a separately scoped HTTP memory-budget change is still warranted. This integration does not claim that residual memory risk is fixed.
- #2143: retain the fork's 32-event cooperative batches plus beta.5's per-SSE yield, rather than replacing them with a yield on every raw event. Existing burst/acknowledgement/isolation tests cover the retained scheduler contract; no claim is made that every-event fairness was imported.
- #2078: include the focused account-health fix at `3d34092d94dcb2f6b66099459b2216b6b7368a28` and its original OpenSpec/tests. Its runtime changes touch the quota helper and state-builder evidence checks without migrations, transport-default changes or quarantine edits. Tests must cover handler-to-storage-to-two-replica recovery, exhausted and available windows, stale/same-second credits and ordinary cooldown expiry. Omit the unrelated service import-format-only hunk. The resulting candidate is beta.5 **plus #2078**, not the untouched release tag.

Before importing #2078 runtime code, its database-backed `test_usage_limit_requires_available_windows_before_early_recovery` failed in all four primary/secondary and credits/no-credits cases on the integrated beta.5 fork: the stored status incorrectly became ACTIVE rather than remaining RATE_LIMITED. The separate historical-exhaustion tests already passed (2 cases); they are negative controls, not reproductions. Results are recorded in the temporary `quota-before-recovery.xml` and `quota-before.xml` reports.

The first supplementary PostgreSQL run passed 25 cases but exposed an existing fixture using `reset_at=15_023_672_358`, outside the PostgreSQL INTEGER column range. The exact fixture is present in fork HEAD. The persisted test now uses a deadline twice beyond the accepted reset horizon, still representable by the current schema, and retains the routing/status/reset/block assertions. Pure state-builder tests retain the original arbitrary-size wrong-unit timestamps. This changes only the fixture, not schema or runtime behavior.

## Initial integration failure classification

The broad integration run finished with 2,598 passes, 34 skips and one failure:
`test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope` expected
`hard_affinity_saturated` but received `upstream_request_timeout`. The exact test
passed in isolation on both fork beta.4 and the candidate (about 77 seconds each).
Running the complete sticky-session module with two workers then reproduced the
same failure on both trees: 41 passed and one failed in each run. Both trees used
the same Python/dependency environment and separate synthetic SQLite databases;
the baseline result rules out a beta.5-only source regression, rather than
comparing dependency versions with production.

The relevant streaming retry loop, budgeted selector and test are unchanged from
the fork baseline. Both failing module runs show account selection being
cancelled by its deadline after repeated hard-owner-unavailable results. This
supports a pre-existing deadline-sensitive error-envelope problem, not a
beta.5-only regression. Ownership was not relaxed and no test assertion was
weakened. The operator approved the separate
`fix-selection-deadline-error-envelope` follow-up: derive authenticated scope
before health/model/exclusion/cap filters and skip impossible capacity recovery
for an out-of-scope hard owner. This preserves the established error and avoids
the deadline-sensitive loop without changing genuine timeouts or in-scope
recovery. Virtual timing, public route, bridge and WebSocket regressions cover
that distinction. Final backend runs are recorded in verification.md; isolated
passes do not turn the original broad failing run into a pass.

The operator also approved the separate `fill-canonical-spec-purpose-sections`
documentation change. Its 22 placeholder replacements are verified and archived
under `archive/2026-09-09-fill-canonical-spec-purpose-sections/`. Canonical normal
and strict validation now pass all 59 capabilities using CI-pinned OpenSpec
1.11.0. No requirement was relaxed to bypass either original blocker.

## Verification and operations

Tests use temporary synthetic databases with ambient database/bootstrap settings excluded and an explicit nonexistent env file. Build the candidate Rust helper separately and pass it only to tests. Existing untracked `simplify-install-one-liners/` is untouched. No deployment, production mutation, commit, push or PR is authorized in this change. Global strict OpenSpec validation previously reported 22 unchanged Purpose placeholders; do not weaken that gate or archive an unverified change.
