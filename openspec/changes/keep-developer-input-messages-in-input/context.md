# Context

## Upstream wire shapes

Probed against `chatgpt.com/backend-api/codex/responses` (HTTP, `gpt-6.1-sol`):

| `input` | top-level `instructions` | Result |
|---|---|---|
| developer message + user message | non-empty | 200 |
| developer message + user message | `""` | 200 |
| developer message + user message | absent | 200 |
| user message only | absent | 200 |
| system message + user message | non-empty | 400 `{"detail":"System messages are not allowed"}` |

Hoisting was introduced to avoid that rejection (#950); it only needs to apply to `system` messages.

## Chained responses

`previous_response_id` carries the previous response's `input` and output items forward, but not its `instructions`.
Each request's `instructions` apply to that request only. Moving a developer message from `input` to `instructions`
therefore changes what every chained response sees, not just the request being normalized.

Example (issue #2563 reproduction, about 12.8k tokens of reference data in one developer message, then a chained
question about it):

- Direct: `input_tokens` 12830, answers from the reference data.
- Hoisting proxy: `input_tokens` 34, "I don't have reference data".
- This change: `input_tokens` 12830, answers from the reference data.
