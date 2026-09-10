## Context

A production account was paused at 11:42:14 UTC on 2026-09-10, yet a new direct WebSocket turn started on the same account at 11:45:21. The capability-specific revalidation path does not cover ordinary socket reuse. The shared routing availability cache already represents committed pause, reauthentication, deactivation, and deletion across replicas.

## Goals / Non-Goals

**Goals:** Honor account unavailability for every new response.create, preserve accepted sibling work, and reuse existing settlement and owner-switch paths.

**Non-Goals:** Change replica-local overload policy, reroute account-owned payloads, interrupt already dispatched responses, or introduce a database lookup for each frame.

## Decisions

- Check the shared availability cache alongside the existing required-owner socket-switch condition. An unavailable idle owner retires through the existing cleanup; ordinary connect selection enforces pins and scope. A pending sibling prevents retirement and only the unsent turn is rejected.
- Check availability again at the final send boundary after admission/lease awaits. A late pause produces an account-unavailable terminal and releases the rejected turn's reservation, gate, and create lease through existing cleanup.
- Preserve original client turn-state and previous-response ownership. Discard transport-learned turn-state on retirement exactly as the existing owner-switch path does.

## Risks / Trade-offs

- Cross-replica observation is bounded by the existing invalidation poll, not an atomic database/send transaction. The synchronous final check introduces no additional await before the send begins.
- A client anchored to an unavailable account gets a fail-closed terminal; allowing it to move would violate ownership.
- Existing lightweight test accounts often use an unseeded availability cache. Regression tests must explicitly publish unavailability and cover both movable and owner-bound requests, accepted siblings, and a pause during admission.

## Deployment

Use the existing HA surge rollout after tests and strict OpenSpec validation. No migration is needed. Already accepted work drains under the rollout's existing deadline; operator pauses remain committed throughout.
