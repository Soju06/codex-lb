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
- [x] 2.5 Propagate relay-owner cancellation and suppress replay after session
      closure.
- [x] 2.6 Seal the transport proof for a socket closed before dispatch and
      transparently retry that exact request once on the leased account.
- [x] 2.7 Apply the same bounded exact resend to the direct WebSocket proxy
      while preserving sole-owner, admission, and account-lease constraints.

## 3. Verification

- [x] 3.1 Add regressions for first-timeout recovery, telemetry-only silence,
      cancellation-race acknowledgement, leased-account routing, unsafe replay,
      second-timeout settlement, and relay shutdown during child cancellation.
- [x] 3.2 Run focused bridge tests, lint, format, type, architecture, and strict
      OpenSpec validation.
- [x] 3.3 Review the final diff for replay widening, duplicate settlement,
      affinity movement, account penalties, and unrelated edits.
- [x] 3.4 Add adapter construction/dispatch proofs and an externally visible
      compacted-continuation regression for a closed warm socket.
- [x] 3.5 Add direct WebSocket regression coverage for transparent
      closed-before-send continuation recovery.
