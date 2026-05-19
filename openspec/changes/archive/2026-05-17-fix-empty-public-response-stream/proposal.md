# Fix Empty Public Response Streams

## Why

OpenAI-provider clients can receive a successful `/v1/responses` stream that contains terminal usage and output metadata but no renderable text deltas when upstream only emits final message content in item/terminal events.

## What Changes

- Synthesize a public `response.output_text.delta` event from final message output when no text delta was already observed for that output item.
- Keep the original terminal/item events so existing clients that consume full Responses objects continue to work.

## Impact

- Improves compatibility for OpenCode's built-in OpenAI provider with a `baseURL` override.
- Does not alter streams that already include text deltas.
