## ADDED Requirements

### Requirement: Responses Lite signaling disables parallel tool calls

Every final upstream payload advertised as Responses-Lite through canonical
HTTP signaling or a body-derived/trusted WebSocket marker MUST contain boolean
`parallel_tool_calls=false`. Normalization MUST be idempotent and preserve
input, tools, cache keys, and unrelated reasoning values. It MUST NOT establish
Lite trust. Non-Lite requests MUST retain their existing parallel-tool setting.

#### Scenario: Body-derived Lite request

- **WHEN** a request with `additional_tools` omits parallel_tool_calls or supplies null, true, or false
- **THEN** final egress contains boolean false and retains established Lite signaling

#### Scenario: Trusted continuation

- **GIVEN** established same-model Lite continuity
- **WHEN** a trusted marker-only delta omits the original tool prefix
- **THEN** final serialization still sends parallel_tool_calls=false

#### Scenario: Equivalent egress paths

- **WHEN** established Lite uses upstream HTTP, compact, WebSocket, or HTTP fallback
- **THEN** the final body obeys the same false-valued parallel-tool contract

#### Scenario: Non-Lite control

- **WHEN** a request is not advertised as Lite, including one whose untrusted marker was stripped
- **THEN** this normalization does not change its parallel-tool setting
