## Why

The operator-wide `prohibitFastMode` setting currently blocks only priority tiers derived from Fast Mode model aliases. Explicit client `service_tier: "priority"` values, including Codex WebSocket `response.create` frames, still reach OpenAI and bypass the configured policy.

## What Changes

- Treat `prohibitFastMode` as a global prohibition of any service tier that canonicalizes to OpenAI's `priority` tier.
- Apply the prohibition after client input, model-alias normalization, defaults, and API-key enforcement have resolved the outbound tier, before upstream serialization on every Responses request path.
- Give the administrator's global prohibition precedence over an API key's `enforced_service_tier`; prohibited priority tiers are omitted from the upstream payload while non-priority and absent values retain their existing wire behavior.
- Emit an info-level diagnostic containing the request ID and stripped tier whenever the policy removes a priority tier.
- Add regression coverage for HTTP Responses, OpenAI-compatible `/v1` Responses, compact Responses, WebSocket `response.create`, warmup, model aliases, and API-key enforced priority tiers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `fast-mode-policy`: Expand the operator policy from alias-derived Fast Mode tiers to all outbound priority-tier requests and define precedence over API-key enforcement.
- `responses-api-compat`: Require consistent priority-tier prohibition across native HTTP, OpenAI-compatible `/v1`, compact, WebSocket, chat-conversion, and warmup request paths.

## Impact

- Affected policy logic: `app/modules/proxy/request_policy.py` and outbound Responses request preparation/serialization.
- Affected entry points: native and `/v1` Responses, compact Responses, chat-to-Responses conversion, WebSocket `response.create`, and dashboard warmup.
- Existing API-key enforced priority behavior changes only when the administrator has enabled `prohibitFastMode`; all behavior remains unchanged while the setting is disabled.
- No schema, migration, dependency, or changelog changes are required.
