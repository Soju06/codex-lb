# responses-api-compat — Delta

## ADDED Requirements

### Requirement: Only message items are hoisted into instructions

When normalizing Responses or compact request `input`, the service MUST only
hoist items that are instruction messages — items whose `type` is omitted or
`"message"` — into the `instructions` field. Any other typed
`system`/`developer`-role input item MUST be forwarded upstream in its original
position and shape, whether or not the request carries a Responses-Lite
`additional_tools` prefix.

#### Scenario: typed non-message developer item survives normalization

- **WHEN** a client sends a Responses request whose `input` contains a typed
  non-message item with a `developer` role (for example a future upstream item
  type without `content`) alongside developer instruction messages
- **THEN** the instruction messages are hoisted into `instructions`
- **AND** the typed non-message item remains in `input` unchanged

#### Scenario: typeless system messages keep hoisting behavior

- **WHEN** an OpenAI-compatible client sends `input` containing
  `{"role": "system", "content": "sys"}` without a `type` field
- **THEN** that item is hoisted into `instructions` as before
