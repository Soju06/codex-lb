# Drop Unknown Message Fields in Chat → Responses Coercion

## ADDED Requirements

### Requirement: Drop unknown message-object fields during coercion

The service MUST drop unknown keys on a chat message object when coercing the message into a Responses API input message item. Specifically, when a chat message is converted into an `input` message item (role `user` / `assistant` without tool_calls, or the message-content half of an assistant message that also has tool_calls), the emitted item MUST contain exactly the keys `role` and `content`. Other fields on the inbound chat message — the documented but unsupported `name` field, any other standard chat-message field that has no Responses input-item equivalent, and any arbitrary client-supplied key (including keys starting with `_`) — MUST NOT appear on the emitted item.

This matches OpenAI's own `/v1/chat/completions`, which parses the known chat-message fields and silently ignores everything else rather than forwarding it. The Responses API input message item only accepts `role` + `content`; forwarding any other key triggers an upstream `unknown_parameter` rejection.

The Requirement applies only to message-item shapes. The existing tool-call decomposition (`FunctionCallInputItem`) and tool-message conversion (`FunctionCallOutputInputItem`) paths already select fields explicitly and are unaffected.

#### Scenario: Standard `name` field is dropped

- **WHEN** a client sends `{ "model": "...", "messages": [{ "role": "user", "content": "hi", "name": "alice" }] }`
- **THEN** the mapped Responses payload's `input` contains exactly `[{ "role": "user", "content": [{"type": "input_text", "text": "hi"}] }]` with no `name` key on the input item

#### Scenario: Arbitrary client-internal keys are dropped

- **WHEN** a client sends a message carrying client-internal bookkeeping keys, e.g. `{ "role": "user", "content": "hi", "_client_marker": true, "extra": 1 }`
- **THEN** the mapped Responses payload's `input` item for that message contains exactly `role` + `content` and no other keys

#### Scenario: Assistant message with tool_calls drops unknown keys on the message half

- **WHEN** a client sends an assistant message that has both `content` and `tool_calls`, with extra keys on the message object (`name`, `_client_marker`, etc.)
- **THEN** the message-content half of the decomposed input items is `{ "role": "assistant", "content": [...] }` with no `name` / `_client_marker` keys, and the tool-call half remains a well-formed `function_call` input item

#### Scenario: No regression for clean messages

- **WHEN** a client sends a message that already carries only `role` and `content` (the most common case)
- **THEN** the mapped Responses payload's `input` item for that message is byte-equivalent to the previous behavior
