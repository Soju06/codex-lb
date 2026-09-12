## ADDED Requirements

### Requirement: Direct WebSocket full resends can omit response-owned bookkeeping for safe replay

When direct WebSocket continuity verifies a full resend's raw input prefix and injects `previous_response_id`, the proxy SHALL retain an account-neutral projection for replay if the projected suffix retains a completed prior assistant reply followed by fresh input and the complete projected payload passes the existing account-neutral replay validator. The projection SHALL use the existing rules for removing response-owned bookkeeping and item IDs, preserving message content and paired tool calls and outputs.

The normal anchored upstream request SHALL remain unchanged. Projection SHALL NOT make client-supplied anchors, file ownership, turn-state ownership, or requests with already-visible model output eligible for cross-account replay. A resend that fails completeness or portability validation SHALL retain its existing replay behavior.

#### Scenario: Quota rejection before output recovers on another account
- **GIVEN** a completed WebSocket turn and a follow-up resending its verified input prefix, encrypted reasoning, prior assistant reply with a response-owned ID, and a new user message
- **WHEN** the proxy-anchored follow-up receives a quota rejection before response creation
- **THEN** the existing replay path SHALL exclude the exhausted account and send the projected full transcript without `previous_response_id` to an eligible account
- **AND** the client SHALL receive only the replacement response lifecycle

#### Scenario: The next turn matches the original client history
- **GIVEN** a projected replay completes on the replacement account
- **WHEN** the client resends its original history followed by the replacement reply and new input
- **THEN** continuity SHALL match the verified original client prefix and anchor the next turn to the replacement response
- **AND** the trimmed request SHALL NOT resend the old account's reasoning or item IDs

#### Scenario: Projection cannot recover an incomplete or account-bound resend
- **GIVEN** a proxy-anchored resend with response-owned bookkeeping
- **WHEN** projection lacks the retained prior assistant reply or leaves an account-bound reference
- **THEN** projection SHALL NOT authorize cross-account replay
