# responses-api-compat — Delta

## ADDED Requirements

### Requirement: Non-message developer input items are preserved

When normalizing Responses or compact request `input`, the service MUST only
hoist items that are instruction messages — items whose `type` is omitted or
`"message"` — into the `instructions` field. Any other `system`/`developer`-role
input item (such as the Codex responses-lite
`{"type": "additional_tools", "role": "developer", "tools": [...]}` prefix)
MUST be forwarded upstream in its original position and shape.

#### Scenario: additional_tools input item survives normalization

- **WHEN** a Codex client sends a Responses request whose `input` begins with
  `{"type": "additional_tools", "role": "developer", "tools": [...]}` followed
  by developer instruction messages
- **THEN** the developer instruction messages are hoisted into `instructions`
- **AND** the `additional_tools` item remains in `input` unchanged

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before
