## ADDED Requirements

### Requirement: Public Responses streams expose renderable final text
For OpenAI-style streaming `/v1/responses` and `/backend-api/codex/responses`, the service MUST expose renderable `response.output_text.delta` events for assistant message text when upstream provides final text only in output item or terminal response output payloads. The service MUST NOT duplicate text deltas for an output item that already emitted a text delta.

#### Scenario: final output item text is exposed as a text delta
- **WHEN** upstream emits a `response.output_item.done` event with assistant message text and no prior text delta for that output item
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the final item event

#### Scenario: terminal response output text is exposed as a text delta
- **WHEN** upstream emits only a terminal `response.completed` event with assistant message text in `response.output`
- **THEN** the service emits a corresponding `response.output_text.delta` event before forwarding the terminal event

#### Scenario: existing text deltas are preserved without duplication
- **WHEN** upstream already emits a `response.output_text.delta` for an output item
- **THEN** the service forwards the stream without synthesizing another text delta for that same output item
