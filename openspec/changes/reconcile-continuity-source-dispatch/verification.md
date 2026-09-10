# Current-main reconciliation verification

Target: `c0beaaadd96a89f0240582b5449bf4dd50647c7d`.
Previous candidate: `d8e1b7f9fb30f49b4b2bb6dc2c7f5ccba80d1020`.
The conflict-free initial merge tree was `01e1124df73d30e65628675179166a379f28f806`, matching the earlier static composition record. This verification covers that composition plus the review corrections below. The original PR worktree was left untouched.

## Corrections

- Validate synthesized-turn-state context metadata before signing or constructing owner-forward headers. Five illegal-control-character cases all failed on the unchanged application; after the one-line validation addition, the full forwarding suite passes. Existing authenticated marker round-trip and mixed-version signature tests remain controls.
- Check the arguments of both alias lookups in the legacy-forward regression, alongside its exact two-call assertion.
- Move six unfinished verification/delivery tasks and their associated correction tasks from the archived ownership change to the active change. Their hosted-review requirements remain unchecked.
- Remove four retired Settings arguments from the image-bypass fixture. Typing exposed them after current main constantized those controls. Dashboard routing and the fixture's two-request marker assertion remain unchanged.

## Local results

- 3,188 ownership, forwarding, selection, compact, marker and source-composition tests passed; three existing load-balancer version-conflict tests were skipped because per-account locking retired their original premise.
- 509 full HTTP Responses, compact, direct WebSocket, HTTP bridge and promotion-accounting route tests passed.
- Full Ruff check/format and ty passed.
- Proxy architecture, cancellation safety, timing seams and settings-tier checks passed.
- All 64 canonical specifications passed strict validation; the active reconciliation change also passed strict validation.
- Independent read-only review found no additional issue in the scoped correction diff and confirmed current-main setting removals remained intact. The four fixture arguments it identified were the same typing failure corrected above.

Before application imports, every test invocation set `CODEX_LB_DATABASE_URL` and `CODEX_LB_TEST_DATABASE_URL` to the same dedicated absolute temporary SQLite path. The runner verified foreground, background and test engine URLs before pytest. No live database, container, service, UI, deployment or review CLI was used.

An initial settings-tier check imported the reused virtualenv's editable checkout. Re-running with the composition directory explicitly first on `PYTHONPATH` passed. Test runners already put the composition first on `sys.path`; their source location and all three database engines were guarded independently.

## Delivery boundary

The intended publication is a normal merge commit retaining both the previous PR head and the pinned current main as parents. Recheck the remote head and main before pushing. Local success does not clear hosted review threads or prove CI for the new commit; those checks remain delivery work in the active change. Related issue: #2274. No security-exhaustion split was made because current main already handles that condition and the candidate's new classification must preserve its existing consumers.

## Compact release retry correction

- On composition `21cbb6424`, both external compact routes reproduced the leak with two and three injected release failures. Four cases timed out waiting for quota release; zero and one failure controls passed.
- After transferring failed cleanup to the existing tracked release retry, all 176 compact cases in the integration and proxy-utils suites passed. The expanded cleanup-ready test then passed in both ordinary and already-cancelled AnyIO scopes; all four focused settlement cases passed. Both routes confirm the persisted reservation is released and reserved quota returns to zero after retry.
- The retry is scheduled before cleanup readiness, remains owned by the service scheduler, and uses the existing concurrency limit and backoff. Unconfirmed settlement still reports `usage_settlement_failed` and does not flush health writes. Confirmed-release owner errors and forwarded receiver settlement controls remain unchanged.
- Ruff lint, formatting, diff whitespace, and strict active-change validation passed. All test imports and resets used matching foreground, background, and test SQLite engine URLs under a dedicated absolute temporary database path.

## Fail-closed main and bounded cleanup follow-up

Main `6d11e560` removes server-owned ambiguous recovery; the merge removes the now-unused retry reservation helper and preserves owner fences and the full-body placeholder predicate. The merged source passed 3,221 affected tests and static checks. Section 14's indefinite retry correction is superseded by `2026-09-10-bound-compact-failed-cleanup`: a red diagnostic retained 12 tasks for 12 failures while persistence drain reported complete. The new correction retains shielded settlement/fail-safe release and durable accounted reservations for existing stale reclamation, without detached retries or new thresholds.

179 compact cases pass after correction, including both routes, repeated double-write failures, cancellation, confirmed-release controls and exactly-once stale quota restoration. Both independent review axes found no mismatch at `6943b154` / tree `0c206b59`. Lint, typing, 65 strict specs and strict docs build pass. Tests used dedicated matching database URLs with engines checked before imports. Hosted current-head results remain a separate delivery gate.
