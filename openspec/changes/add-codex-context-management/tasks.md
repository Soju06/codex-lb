## Implementation
- [x] Add authenticated context routing, ownership, bounded fan-out and trusted result replay.
- [x] Preserve scope, cancellation and replay boundaries with integration tests.
- [x] Fix dashboard request-log schema and add a mixed-page regression.
- [x] Rebase onto current main and consolidate OpenSpec into one active change.
- [x] Keep the deployed context migration ancestry and join it to upstream with one merge head.
- [x] Route clocks, deadlines and fan-out through injected collaborators.
- [x] Remove duplicate frame parsing and repeated context database work.
- [x] Add focused regressions for cache behavior, ownership, transactions and injected scheduling.

## Verification
- [x] Run affected context/transport/migration tests and relevant static guards.
- [x] Validate the active change and owning specs.
- [x] Update the PR description with evidence and design tradeoffs.
- [ ] Respond to the maintainer after the architecture decision.

Full CI runs in GitHub Actions. Local verification here is scoped to the affected behavior; earlier test counts belong to earlier commits and are not cumulative.

## Fork compatibility and current-main integration
- [x] Preserve current-main routing, streaming, tests and contributor changes while resolving conflicts.
- [x] Permit authenticated same-key result replay into canonical history-enabled fork sessions; preserve target ownership and account-scope checks.
- [x] Cover HTTP, bridge and native WebSocket fork replay, foreign keys/sessions, and separate notes operations.
- [x] Restore the deployed context migration parent and add a merge revision with current main.
- [x] Verify fresh upgrades and upgrades from the deployed context branch preserve context rows and apply missing upstream migrations.
- [x] Validate the isolated candidate against a consistent copy of production state and native client fork continuation.

## September 10 upstream refresh
- [x] Integrate upstream `aa75e1c1`, preserving context ownership and the updated bridge fail-closed behavior.
- [x] Join the previous context merge and the new dashboard migration chain without rewriting either ancestry.
- [x] Verify upgrades from the deployed context revision, the previous local merge and the new upstream head, including existing dashboard credentials.
- [x] Run the affected context, bridge, dashboard access and migration checks, then refresh the isolated candidate evidence.

## September 11 publication compatibility
- [x] Integrate upstream `4096c18a` and retain the context fork contract.
- [x] Join the deployed context/dashboard head with the new authentication-provider migration chain.
- [x] Verify upgrades from the deployed head and new upstream head, preserving identities, credentials and context ownership.
- [x] Refresh affected tests and isolated native recovery evidence before the production cutover.

## September 11 review corrections
- [x] Apply the context response limit before returning native `thread_hint` results.
- [x] Convert malformed upstream JSON and text encoding to private HTTP 502 errors for single-account and pooled results; retain sibling cancellation.
- [x] Classify non-string input item types as ineligible for cross-account replay without raising an exception or changing the dispatched body.
- [x] Keep file-reference detection safe for those same non-string types without losing content file pins.
- [x] Remove the unused private-context control adapter while preserving Realtime handling.
- [x] Run focused HTTP regressions, affected context/replay/Realtime tests and static/spec validation.
