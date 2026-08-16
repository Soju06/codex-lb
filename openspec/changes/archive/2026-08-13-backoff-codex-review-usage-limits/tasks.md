## 1. Usage-limit backoff

- [x] 1.1 Track per-sender Codex usage-limit and normal-response timestamps from classified PR timelines (`CodexReviewUsageBackoff`).
- [x] 1.2 Skip `@codex review` posts while the sender's latest Codex response within the backoff window is a usage-limit reply; surface a write warning naming the evidence.
- [x] 1.3 Probe after posting when no quota evidence exists: wait, reread the PR timeline, and latch off remaining triggers on a usage-limit reply; stop probing after the first observed normal response.
- [x] 1.4 Report apply-loop status and errors per decision (`decision.repo`/`decision.number`), not the stale classification-loop variables.
- [x] 1.5 Anchor usage-limit detection to the real quota envelope ("You have reached your Codex usage limits" at start of body) so reviews discussing usage limits do not latch.
- [x] 1.6 Count clean THUMBS_UP reactions by Codex reviewers on the sender's request comments as normal responses for unlatching.
- [x] 1.7 Share backoff state across all `--repo` arguments in one run, retaining classified timelines from repositories without their own triggers as evidence.
- [x] 1.7b Gather the repository's recent issue comments (within the window, grouped per issue) as additional quota evidence before posting, covering single `--pr` runs and closed PRs; degrade with a warning if the gather fails.
- [x] 1.8 Resolve the sender installation-token-compatibly (workflow-exported `GH_APP_SLUG` -> `<slug>[bot]`, `GET /user` fallback); on failure, disable only the trigger path with per-decision warnings while label sync proceeds.
- [x] 1.9 Reclassify each PR immediately before performing its writes: skip superseded heads with a warning, apply same-head evidence changes fresh, and trigger only when both classifications agree. Feed the fresh timeline into the shared backoff and honor `--tolerate-read-errors` for reclassification failures.
- [x] 1.10 Suppress review triggers (and ignore the app slug) once the run has switched to `GH_FALLBACK_TOKEN`, since posted comments would no longer be authored by the resolved sender; never silently retry the review-request POST itself under the fallback token.
- [x] 1.11 Probe for quota replies only when the review request was actually posted (skip the wait/reread after tolerated write denials).

## 2. Validation

- [x] 2.1 Unit coverage: latch on recent limit, unlatch on newer normal reply, per-sender independence, probe latch across PRs (asserting per-PR apply lines), probe suppression after a normal response.
- [x] 2.1b Unit coverage for the review-fix hardening: anchored quota envelope matching, clean-reaction unlatch, cross-repo backoff persistence (including non-triggering repos), app-slug sender resolution and degraded trigger-only skip, fallback-token trigger suppression, apply-time reclassification (head move and same-head evidence change).
- [x] 2.2 Run the sync-script unit suite.
- [x] 2.3 Validate with `openspec validate backoff-codex-review-usage-limits --strict`.
