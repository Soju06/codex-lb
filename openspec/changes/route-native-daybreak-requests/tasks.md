## Implementation
- [x] Persist a per-key toggle, default off, through API/cache and dashboard create/edit.
- [x] Recognize legacy Blue models and the native cyber selector using existing Trusted Cyber eligibility.
- [x] Support WebSocket, HTTP Responses and compact while preserving native fields, key scope, lineage, ownership and retry constraints.
- [x] Reject malformed, duplicate, conflicting and unsupported native selectors for opted-in keys.
- [x] Update normative specs, context and client documentation.

## Verification
- [x] Validate routing, API-key lifecycle, replay and migration regression tests on current main.
- [x] Run the relevant lint, type, dashboard, documentation and OpenSpec checks.
- [x] Review the final feature diff and add current-baseline before/after screenshots and a routing diagram.

## Conversation isolation correction
- [x] Add failing HTTP/compact and WebSocket regressions for ordinary parent/sibling routing, shared session identifiers, same-conversation persistence, and mid-session switching.
- [x] Restrict capability aliases to the conversation and direct continuations, and prevent DBB from rewriting ordinary process locality.
- [x] Verify socket transitions, ownership, restricted retries, persistence failures, and existing routing rules.
Deployment and fork-PR publication are tracked separately in the operator workflow; they do not change this routing contract.
