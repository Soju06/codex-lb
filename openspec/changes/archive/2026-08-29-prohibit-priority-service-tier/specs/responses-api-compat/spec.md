## ADDED Requirements

### Requirement: Responses transports apply the global priority-tier prohibition consistently

The service MUST apply the same canonical priority-tier prohibition before
forwarding an upstream payload from native `/responses`, OpenAI-compatible
`/v1/responses`, native and `/v1` compact Responses, chat-to-Responses
conversion, WebSocket `response.create`, dashboard warmup, and internal
owner-forwarding paths whenever `prohibitFastMode` is enabled. WebSocket
`response.create` frames MUST use the `prohibitFastMode` policy snapshot
captured when the connection began.

#### Scenario: Native and OpenAI-compatible HTTP requests omit explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** `/responses` or `/v1/responses` receives an explicit priority service tier
- **THEN** the effective upstream payload omits `service_tier`

#### Scenario: Compact requests omit explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** `/responses/compact` or `/v1/responses/compact` receives an explicit priority service tier
- **THEN** the effective upstream payload omits `service_tier`

#### Scenario: Chat conversion omits explicit priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** a compatible chat-completions request carries an explicit priority service tier and is converted to Responses
- **THEN** the effective upstream Responses payload omits `service_tier`

#### Scenario: Owner forwarding cannot restore priority

- **GIVEN** `prohibitFastMode` is enabled
- **WHEN** an internal owner-forwarded payload carries a priority service tier
- **THEN** the receiving preparation boundary omits `service_tier` before upstream forwarding
