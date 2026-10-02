## ADDED Requirements

### Requirement: HTTP bridge retry health preserves upstream reset metadata

When an HTTP bridge retry path records a rate-limit or quota failure, it MUST
pass the parsed upstream `resets_at` and `resets_in_seconds` fields to the
existing account-health handler. The model-capacity, owner-pinned, and ordinary
pre-created retry branches MUST preserve the same evidence as terminal
settlement. Deferred health writes MUST retain that evidence until API-key
reservation settlement has committed. Existing reset validation and
metadata-free fallback behavior MUST remain unchanged.

Equivalent direct WebSocket retry paths MUST pass the same parsed error
metadata to account-health settlement, including accepted output-free replays.

#### Scenario: A long usage limit permits existing unavailable-owner retirement

- **GIVEN** a hard-affinity HTTP bridge owner receives `usage_limit_reached` with an upstream reset beyond the next request's recovery budget
- **WHEN** the bridge records the retry-path health failure
- **THEN** the persisted account reset reflects the upstream deadline rather than a metadata-free fallback
- **AND** a later eligible request can retire that unavailable owner and complete on another selectable account through the existing retirement path
- **AND** explicit client anchors, file pins, and account-neutral replay proof requirements remain enforced

#### Scenario: Keyed retry preserves metadata until settlement

- **GIVEN** a pre-created bridge request has an unsettled API-key reservation
- **WHEN** upstream returns a limit error containing absolute or relative reset metadata
- **THEN** the bridge queues that metadata with the health penalty
- **AND** it does not write account health until reservation settlement commits

#### Scenario: Missing or invalid metadata keeps existing cooldown behavior

- **GIVEN** an upstream limit error has missing or invalid reset metadata
- **WHEN** a bridge retry records account health
- **THEN** the existing validation and Retry-After or bounded backoff determine the cooldown
