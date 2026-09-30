## Why

PR #2429 conflates request access-token freshness with idle refresh-token keepalive. Replacing the twelve-hour guardian gate with eight days removes protection previously added for idle and paused accounts.

## What Changes

- Preserve a fixed twelve-hour guardian keepalive independent of the eight-day request freshness policy.
- Use one guardian predicate for candidate selection and the fresh per-account recheck, retaining forced exchange only after admission.
- Keep six-hour scans, oldest-first 100-account admission, backoff, leader gates, paused routing exclusion, and cancellation-safe settlement.
- Correct dashboard descriptions and stable context; explicitly mark the earlier shared-window proposal superseded.
- Add threshold, request-policy independence, and fresh-row handoff regressions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: restore independent twelve-hour idle keepalive with precise scan/admission semantics.

## Impact

Auth Guardian, its unit/integration coverage, existing translations, and OpenSpec. No settings, schema, request-path refresh changes, or production operations. The removed per-scheduler maximum-age argument is not reintroduced as a second runtime policy knob.
