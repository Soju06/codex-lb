## Why

Codex harness clients encode Fast Mode in qualified model aliases such as
`gpt-5.6-sol-xhigh-fast`. Operators need a global setting that keeps those
requests on the same base model and reasoning level while ensuring the OpenAI
upstream never receives the Fast/priority tier.

## What Changes

- Add a persisted dashboard setting, disabled by default, that prohibits Fast
  Mode for proxied model requests.
- When enabled, normalize qualified `-fast` model aliases to their canonical
  model and requested reasoning effort, but omit their derived priority service
  tier before model-source selection, quota reservation, and OpenAI forwarding.
- Expose the setting in the routing settings dashboard and through the settings
  API.

## Capabilities

### New Capabilities

- `fast-mode-policy`: Operator-controlled prohibition of Fast Mode in qualified
  Codex model aliases.

### Modified Capabilities

- `responses-api-compat`: Qualified model alias normalization gains an
  operator-level policy that can suppress the derived Fast/priority tier.
- `frontend-architecture`: The routing settings contract exposes the Fast Mode
  prohibition control.

## Impact

- Dashboard settings persistence, API schema, service, cache invalidation, and
  routing UI.
- Responses and chat-completions policy enforcement, including Codex WebSocket
  harness requests.
- Backend and frontend regression coverage plus a database migration.
