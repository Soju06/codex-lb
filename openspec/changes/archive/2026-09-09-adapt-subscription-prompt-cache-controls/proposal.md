# Adapt subscription prompt-cache controls

## Why

The public OpenAI Responses API supports GPT-5.6 explicit prompt caching through
`prompt_cache_options` and per-content `prompt_cache_breakpoint` markers. The
Codex subscription upstream currently rejects both controls before response
creation. A client using the documented public shape therefore receives a 400
through codex-lb even though the same request can still use subscription-side
implicit caching and `prompt_cache_key` affinity.

Model-source requests are different: an OpenAI-compatible API-key source may
support the public controls and must receive them unchanged. The adaptation
therefore belongs at the subscription egress boundary, not in shared request
validation.

## What Changes

- Subscription Responses egress omits `prompt_cache_options` and explicit
  breakpoint markers while preserving prompt content, order, and
  `prompt_cache_key`.
- Successful HTTP responses that used this fallback expose
  `X-Codex-LB-Prompt-Cache-Mode: subscription-implicit` so clients do not
  mistake the fallback for exact explicit-prefix caching.
- OpenAI-compatible model-source egress preserves the explicit controls.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: documented prompt-cache controls are adapted only for
  the subscription upstream and their semantic downgrade is observable.

## Impact

- Code: Responses request serialization and HTTP route response metadata.
- Tests: subscription and model-source regressions at `/v1/responses`.
- API/schema: one informational response header; no database or configuration
  change.
