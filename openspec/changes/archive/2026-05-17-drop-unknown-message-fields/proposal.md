# Drop Unknown Message Fields in Chat → Responses Coercion

## Problem

codex-lb's `/v1/chat/completions` accepts unknown message-object fields (the standard chat `name` field, arbitrary client-internal bookkeeping keys, etc.) and forwards them verbatim into the upstream Responses API `input` items. The upstream Responses API rejects those keys with a hard error:

```
502 invalid_request_error:
  [ObjectParam] [input[N]._<key>] [unknown_parameter]
  Unknown parameter: 'input[N]._<key>'
```

OpenAI's own `/v1/chat/completions` does not behave this way — it parses the documented chat-message fields and silently drops everything else. codex-lb advertises chat-completions compatibility, so client code written against the OpenAI contract (which often attaches its own message-object bookkeeping keys, or sets the documented standard `name` field) breaks against codex-lb but works against OpenAI.

Concrete failure observed against `https://codex.nekos.me/v1/chat/completions` (Hermes Agent, 2026-05-15):

1. Client appends two synthetic recovery messages carrying an internal marker key `_empty_recovery_synthetic` (a normal pattern: bookkeeping flags on the local message buffer that the client never expected to wire).
2. codex-lb forwards the marker into the Responses `input` items.
3. Upstream returns `502 unknown_parameter`.
4. Because the marker persists in the client's session buffer, every subsequent request in that session replays the poisoned input → identical 502 → retry storm.

The standard chat `name` field has the same problem: every message that carries `name` produces a 502.

## Solution

Treat message-object coercion as a strict translation: a Responses API input message item carries only `role` + `content`, and that's all we should emit. Everything else on the inbound chat message — known fields used elsewhere (`tool_calls`, `tool_call_id`, `refusal`), unsupported standard fields (`name`), and arbitrary client-supplied keys — is consumed during coercion or dropped.

The fix is narrowly scoped to `app/core/openai/message_coercion.py::_normalize_message_content`: replace the previous "copy the message dict and overwrite `content`" pattern with explicit field selection. The function now emits `{"role": role, "content": normalized_content}` and nothing else. Refusal is folded into content as a `refusal` part (existing behavior); tool_calls flow through their existing decomposition path (existing behavior); `name` / `_*` / any other key is dropped.

This matches OpenAI's chat-completions semantics: known fields are parsed, unknown fields are silently ignored.

## Why this is correct as a behavior change

- The chat-completions compatibility capability already promises "OpenAI Chat Completions expectations" as the contract. OpenAI ignores unknown message-object keys; codex-lb should too.
- The Responses API input message item schema does not include `name` or arbitrary keys — forwarding them was always a violation of the upstream contract, just one that happened to work for clients that never set those fields.
- No client could have been relying on the previous behavior in any useful way: any request that exercised it returned `502 unknown_parameter` and failed.

## Changes

### Spec deltas
- `chat-completions-compat`: add one Requirement covering the silent-drop semantics for unknown message-object fields, with scenarios for `name`, client-internal bookkeeping keys, and the assistant + tool_calls case.

### Code
- `app/core/openai/message_coercion.py::_normalize_message_content` — emit `{role, content}` explicitly.

### Tests
- `tests/unit/test_chat_request_mapping.py` — three new tests:
  - `test_chat_unknown_message_keys_are_dropped` (general unknown-key case + reproducer for the `_empty_recovery_synthetic` field that triggered the original report)
  - `test_chat_message_name_field_is_dropped` (standard `name` field)
  - `test_chat_assistant_tool_call_message_drops_unknown_keys` (assistant + tool_calls path)

## Out of scope

- Changing the OpenAI-compat status code for `unknown_parameter` errors that escape via other paths (the proxy already converts upstream `invalid_request_error` to 400 in the chat-completions adapter).
- The proxy's tool-call message + tool message paths already select fields explicitly (`call_id` / `name` / `arguments` / `output`), so they don't carry unknown keys today.
