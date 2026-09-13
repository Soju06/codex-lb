## Why

Source routing filters on `is_enabled` inside the lookup itself
(`ModelSourcesRepository.find_chat_source_for_model` /
`find_responses_source_for_model`), so a disabled source and a model nobody
configured produce the same answer: `None`. Both then fall through to
subscription account selection, and the subscription upstream rejects the
request:

```
The '<model>' model is not supported when using Codex with a ChatGPT account.
```

On a live instance this repeated hundreds of times per hour for a model whose
only source had been switched off: every attempt selected a ChatGPT account,
spent its health signal on a request no account could serve, and told the
caller nothing about the source that was actually off.

This is the same failure #1658/#1659 fixed for the WebSocket transport — a
source-owned model reaching a subscription account — reached through a
different door. The HTTP routes need the same guarantee, and it must be stated
in terms an operator can act on.

## What Changes

- Add `only_disabled` to the chat and Responses source lookups. It inverts the
  enabled-state filter and leaves every other rule — candidate order, API key
  model allowlist, source assignment scope, subscription-registry precedence,
  route shape, streaming — untouched, so a hit is exactly "the source this
  request would have used, had the operator not switched it off".
- Refuse such a request on `/v1/chat/completions`, `/v1/responses`, and
  `/backend-api/codex/responses` with `503` and error code
  `model_source_disabled`, instead of falling through to subscription routing.
- Leave every other miss on its existing path: a model no source claims, a
  source scoped away from the API key, a route-shape mismatch, and a
  subscription slug that an unscoped key never source-routes all behave exactly
  as before.

## Capabilities

### Modified Capabilities

- `responses-api-compat`
