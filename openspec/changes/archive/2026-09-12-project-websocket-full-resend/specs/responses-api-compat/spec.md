## ADDED Requirements

### Requirement: Direct WebSocket full resends can omit response-owned bookkeeping for safe replay

When direct WebSocket continuity verifies a full resend's raw input prefix and injects `previous_response_id`, the proxy SHALL retain the original replay body subject to the existing size guard. Account-switch preparation SHALL project that body when all eligibility predicates hold: its complete input still matches the original client fingerprint, the projected suffix retains a completed prior assistant reply followed by fresh input, and the complete projected payload passes the existing account-neutral replay validator. The projection SHALL use the existing rules for removing response-owned bookkeeping and item IDs, preserving message content and paired tool calls and outputs.

The normal anchored upstream request SHALL remain unchanged. Projection SHALL NOT make client-supplied anchors, file ownership, turn-state ownership, or requests with already-visible model output eligible for cross-account replay. A resend that fails completeness or portability validation SHALL retain its existing replay behavior.

#### Scenario: Quota rejection before output recovers on another account
- **GIVEN** a completed WebSocket turn and a follow-up resending its verified input prefix, encrypted reasoning, prior assistant reply with a response-owned ID, and a new user message
- **WHEN** the proxy-anchored follow-up receives a quota rejection before response creation
- **THEN** the existing replay path SHALL exclude the exhausted account and send the projected full transcript without `previous_response_id` to an eligible account
- **AND** the client SHALL receive only the replacement response lifecycle

#### Scenario: Canonical Lite resends retain transport reasoning context
- **GIVEN** an eligible body-derived Responses-Lite full resend
- **WHEN** projection validates its transport payload
- **THEN** it SHALL accept and retain the canonical `reasoning.context = "all_turns"` while validating all other reasoning controls

#### Scenario: Keyed quota replay waits for settlement before health writes
- **GIVEN** an eligible full resend with an open API-key usage reservation
- **WHEN** a quota rejection before response creation triggers replay
- **THEN** the exhausted account SHALL be excluded without waiting for a health penalty
- **AND** its health penalty SHALL remain deferred until settlement or release succeeds
- **AND** failed settlement SHALL NOT write the deferred penalty

#### Scenario: The next turn matches the original client history
- **GIVEN** a projected replay completes on the replacement account
- **WHEN** the client resends its original history followed by the replacement reply and new input
- **THEN** continuity SHALL match the verified original client prefix and anchor the next turn to the replacement response
- **AND** the trimmed request SHALL NOT resend the old account's reasoning or item IDs

#### Scenario: Projection cannot recover an incomplete or account-bound resend
- **GIVEN** a proxy-anchored resend with response-owned bookkeeping
- **WHEN** projection lacks the retained prior assistant reply or leaves an account-bound reference
- **THEN** projection SHALL NOT authorize cross-account replay

#### Scenario: Same-account retries retain response-owned state
- **WHEN** a stale-anchor or accepted capacity retry remains on the owner account
- **THEN** it SHALL use the retained body with its reasoning and item IDs intact

#### Scenario: Size-slimmed input cannot preserve the client fingerprint
- **GIVEN** the size guard replaced historical tool output or image content in the retained body
- **WHEN** account-switch preparation compares that input to the client fingerprint
- **THEN** it SHALL reject projection
- **AND** a same-account replay SHALL refresh its fingerprint from the actual retained body
