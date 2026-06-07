## ADDED Requirements

### Requirement: Reset-window preference selection
When earlier-reset routing preference is enabled, the account selector SHALL
support choosing which quota window drives reset-time ordering. The supported
windows SHALL be `primary` and `secondary`. The default SHALL be `secondary` to
preserve existing behavior.

#### Scenario: Primary reset window is selected
- **GIVEN** two healthy eligible accounts with different primary reset times
- **AND** earlier-reset preference is enabled with reset window `primary`
- **WHEN** account selection evaluates otherwise comparable candidates
- **THEN** the account with the earlier primary reset is preferred

#### Scenario: Secondary reset window remains the default
- **GIVEN** earlier-reset preference is enabled without an explicit reset-window override
- **WHEN** account selection evaluates otherwise comparable candidates
- **THEN** the account selector uses secondary-window reset ordering

### Requirement: Reset-window preference propagation
All proxy account-selection surfaces SHALL pass the configured reset-window
preference into the canonical load balancer. This includes HTTP responses,
WebSocket responses, bridge requests, compact requests, transcription requests,
file-backed responses, Codex control requests, and sticky fallback selection.

#### Scenario: WebSocket selection uses the configured window
- **GIVEN** dashboard settings set the reset-window preference to `primary`
- **WHEN** a WebSocket response request selects an account
- **THEN** the load balancer receives `primary` as the reset-window preference
