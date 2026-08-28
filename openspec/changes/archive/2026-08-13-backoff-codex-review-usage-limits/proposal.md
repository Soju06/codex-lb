## Why

The Codex label sync posts `@codex review` for every merge-ready PR missing a current-head review. When the Codex account behind the sender has exhausted its usage limits, Codex replies "You have reached your Codex usage limits" — and the next sync run re-fires `@codex review` anyway, burning the shared quota further and spamming PR timelines. Observed on 6+ PRs during the week of 2026-08-03 (including #1599). Takeover of #1560 (Komzpa), whose author is unresponsive since 2026-07-31.

## What Changes

- The sync script attributes Codex usage-limit replies to the comment sender that triggered them and skips further `@codex review` posts for that sender while a usage-limit reply is the sender's latest Codex response within a backoff window (default 24h, `--codex-usage-limit-backoff-hours`). Usage-limit detection is anchored to the real quota envelope (body starts with "You have reached your Codex usage limits"), so reviews that merely discuss usage limits do not latch the backoff.
- A newer normal Codex response for the same sender unlatches the backoff; a clean THUMBS_UP reaction by a Codex reviewer on the sender's request comment counts as a normal response. Other senders' limits and responses are independent. Backoff state is shared across all `--repo` arguments in one run, classified timelines from repositories without their own triggers still contribute evidence, and before posting in a repository the script also gathers the repository's recent issue comments (grouped per issue) so quota replies outside the selected PRs — single `--pr` runs, closed PRs — still latch.
- When no quota evidence exists, the script posts the first `@codex review`, waits briefly (`--codex-review-response-wait-seconds`, default 10s), rereads that PR's timeline, and suppresses the remaining review requests if the probe hit the usage limit. Probing stops after the first normal Codex response is observed and is skipped when the request was not actually posted (tolerated write denial).
- The sender identity is resolved installation-token-compatibly: the workflow exports the App slug (`GH_APP_SLUG`, sender `<slug>[bot]`) and the script only falls back to `GET /user` for PAT-backed runs. A sender-resolution failure disables only the review-trigger path (warning per decision); label sync proceeds. Once the run switches to `GH_FALLBACK_TOKEN`, the slug is ignored and review triggers are suppressed because the comment author would no longer match the resolved sender; the review-request POST itself is never silently retried under the fallback token.
- Immediately before performing writes for a decision, the script reclassifies the PR and acts on the fresh evidence: head moves skip the decision with a warning, same-head evidence changes are applied fresh, a trigger fires only when both classifications want it, the fresh timeline feeds the shared backoff, and reclassification read failures honor `--tolerate-read-errors`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: label sync gains a usage-limit backoff for `@codex review` triggers.

## Impact

- Code: `.github/scripts/sync_codex_ok_labels.py`, `.github/workflows/codex-review-labels.yml`
- Tests: `tests/unit/test_sync_codex_ok_labels.py`
- Specs: `openspec/specs/github-automation/spec.md`
