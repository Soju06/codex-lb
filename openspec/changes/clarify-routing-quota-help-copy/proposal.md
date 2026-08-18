## Why

Issue #1708: the dashboard collapses several routing concepts into similar
wording, so operators cannot tell what codex-lb will do during a failure
without reading source code. `Sticky threads` sounds like it controls all
affinity (it does not control hard Codex continuation ownership), the sticky
thresholds are percent **used** while account pages show percent
**remaining**, primary/secondary quota windows are never named, and
`Prefer earlier reset` and `Limit warm-up` do not say what they change.
`Active` also reads as "will serve the next request" even though eligibility
is decided per request.

## What Changes

- `Sticky threads` copy states it is a soft preference and adds a note that
  hard Codex continuation affinity (turn state, previous responses, uploaded
  files) is not disabled by the toggle.
- A `Primary vs secondary quota` explainer names the 5-hour and
  weekly/monthly windows and states the used-vs-remaining unit split.
- Sticky threshold descriptions name the window and unit (percent used) and
  render a live `X% used · equivalent to Y% remaining` hint whose two values
  always sum to 100.
- `Prefer earlier reset` copy describes the actual selection behavior
  (earliest reset bucket of the selected window, day-bucketed weekly
  comparison, applied under capacity weighted, usage weighted, and fill
  first).
- `Limit warm-up` copy states that a probe is one small real request using
  the configured model/prompt and consumes a small amount of quota.
- The `Active` status badge on the accounts list carries a hint that the
  displayed status is not per-request eligibility.
- `docs/routing.md` gains a routing/quotas/eligibility explainer section.
- i18n keys added/updated for `en`, `ko`, and `zh-CN`.

Deferred (needs selector plumbing, out of scope here): per-account
"why wasn't this account selected" inspection and an actionable
`No available accounts` breakdown.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `frontend-architecture`: routing/quota help copy, threshold unit hints, and
  the Active-status eligibility hint.

## Impact

Dashboard SPA copy and help text plus `docs/routing.md`; i18n
(`en`/`ko`/`zh-CN`). No API, database, proxy, or routing behavior changes.
