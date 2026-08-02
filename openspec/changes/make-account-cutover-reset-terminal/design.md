# Design: terminal reset after account-pool cutover

## Context

Hard continuity references are account-scoped. A Dashboard assignment change can permanently remove the account that owns `previous_response_id`. The bridge already retains enough metadata to prove when an inbound full resend is self-contained and can be projected into an account-neutral fresh request.

## Decision

Use the existing safe-replay classifier as the only cross-account recovery gate:

1. If a verified full resend includes the retained prior assistant output and has no account-scoped conversation or file dependency, strip the old anchor and session-affinity headers, select a currently assigned account, and replay once.
2. Otherwise fail before unsafe cross-account dispatch with HTTP 400, OpenAI error type `invalid_request_error`, code `continuity_reset_required`, parameter `previous_response_id`, and an explicit `/new` instruction.
3. Keep non-cutover owner failures on the existing retryable 5xx path because those owners may recover.

## Why HTTP 400

The failure is permanent for the current request shape: retrying the same body cannot restore the removed account-scoped continuation. A client error status prevents transport retry loops while the stable error code and parameter explain the required recovery.

## Safety

- The current request is never automatically replayed unless the existing self-contained replay proof succeeds.
- Tool outputs, files, encrypted state, and opaque conversation references remain owner-bound.
- The original sticky mapping is not rebound by an unsafe request.
- The response exposes no account identifiers or raw continuity metadata.
