## Why

Clients should use one endpoint and one API key for ordinary and Daybreak work. Daybreak requests already identify their intent through the legacy model or native access-program field; a custom capability header should not be required. This implements Soju06/codex-lb#2703.

## What Changes

Add a per-API-key automatic Daybreak routing toggle, disabled for existing and new keys until enabled. When enabled, recognize the legacy `gpt-daybreak-blue-latest` model and explicit native `access_programs.cyber = "daybreak_blue"`. Reuse existing Trusted Cyber eligibility, API-key scope, lineage, retry and ownership constraints. Forward native fields unchanged. Validate malformed/conflicting/unsupported native selectors, protect safe replay, and apply the same mandatory eligible-pool constraint to HTTP Responses and compact requests. Preserve custom-carrier intent detection when the toggle is off; capability persistence uses the same conversation boundary for both carriers.

## Capabilities

### Modified Capabilities
- `api-keys`: persisted per-key toggle, dashboard create/edit and cache invalidation.
- `responses-api-compat`: automatic Daybreak intent, forwarding and unsupported transport denial.
- `account-routing`: automatically detected intent narrows the permitted account pool without rewriting ordinary process affinity.
- `sticky-session-operations`: restrict inheritance to the DBB conversation and direct continuations.

## Impact

One Boolean column and migration, API-key lifecycle/UI, request intent parsing, Responses WebSocket and direct HTTP/compact preparation, replay validation, tests and docs. Based on current upstream main, with the migration following its existing head. Actual client delivery of native selectors remains a separate client compatibility concern (#2580). Daybreak Red support is not inferred from the existing Blue eligibility toggle.

## HTTP transport decision

Required HTTP Responses and compact requests bypass external model sources and the HTTP session bridge, using the existing direct subscription transport with a mandatory eligibility constraint. Ordinary traffic retains its transport policy. Account ownership and key scope still apply. Required retry exhaustion never enables ordinary selection.

## Conversation isolation correction

The prior task-wide aliases let one DBB child constrain its ordinary parent and
siblings. Capability requirements now follow an exact conversation and direct
response continuations. For example, task:2 can remain DBB while task:1 and
task:3 retain ordinary routing. Frame window metadata overrides compatibility
headers; thread-id is the fallback before shared session/turn identities.

DBB selection uses the existing thread affinity without seeding the shared
process preference. This prevents DBB-first traffic from pinning later ordinary
work to its account. Ordinary selection, ownership, scope and retry rules stay
in force. A movable mid-session request can select a capable account; an
incompatible account-owned anchor fails closed. Existing exact conversation
markers remain restricted. Broad legacy markers are retained for clients that
lack a specific identity, but do not constrain newly identified conversations.
