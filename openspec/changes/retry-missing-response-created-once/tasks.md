## 1. Specification

- [x] 1.1 Define the bounded eventless retry and terminal fallback contracts.
- [x] 1.2 Strictly validate the OpenSpec change.

## 2. Implementation

- [x] 2.1 Reduce the eventless acknowledgement cap to 30 seconds.
- [x] 2.2 Cancel the stale receive and invoke one existing safe pre-created
      replay before terminal settlement.
- [x] 2.3 Preserve the leased account, hard affinity, file ownership, request
      budget, account neutrality, and whole-session retirement on exhaustion.
- [x] 2.4 Preserve and process a receive result that wins the cancellation race.

## 3. Verification

- [x] 3.1 Add regressions for first-timeout recovery, telemetry-only silence,
      cancellation-race acknowledgement, leased-account routing, unsafe replay,
      and second-timeout settlement.
- [x] 3.2 Run focused bridge tests, lint, format, type, architecture, and strict
      OpenSpec validation.
- [x] 3.3 Review the final diff for replay widening, duplicate settlement,
      affinity movement, account penalties, and unrelated edits.
