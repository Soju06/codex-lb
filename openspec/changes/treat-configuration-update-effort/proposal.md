## Why

The Codex client changes reasoning effort inside a conversation by sending
`configuration_update` input items, for example:

```json
{"type": "configuration_update", "reasoning": {"effort": "high"}}
```

The request-level `reasoning.effort` stays at the value the conversation
started with, so the prompt prefix stays cacheable. codex-lb ignored these
items: request logs, usage accounting, and API-key reasoning policies all read
the request-level value and reported the stale effort for every turn after the
first change.

## What Changes

- Derive the client-selected reasoning effort from the last
  `configuration_update` input item when one is present. It outranks a model
  alias and the request-level `reasoning.effort`.
- Apply that derivation to API-key reasoning allowlist authorization, so a
  disallowed effort written inside an item is rejected with the existing
  `reasoning_effort_not_allowed` contract before quota reservation or upstream
  dispatch.
- Record the same effective effort in request logs, mapped to the wire-safe
  value so logged rows stay comparable with existing rows.
- Keep `configuration_update` items untouched on the wire. They are forwarded
  verbatim to upstream; only the derived effort used for policy, logging, and
  accounting changes.
- No new setting, no schema change, no dashboard UI change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: the API-key reasoning allowlist requirement now
  derives the client-selected effort from a configuration update as well, with
  a defined precedence over model aliases and the request-level field.
- `proxy-runtime-observability`: request logs persist the effective
  client-selected effort instead of the stale request-level value.

## Impact

- Affected code: `app/modules/proxy/request_policy.py` (derivation helper,
  precedence, log helper), `app/modules/proxy/streaming/*`,
  `app/modules/proxy/compact.py`, `app/modules/proxy/http_bridge/*`
  (request-log call sites), `app/modules/api_keys` enforcement consumers
  through the shared derivation.
- Affected tests: unit coverage for derivation, precedence, validation, and
  log mapping; one integration test through the Responses product path that
  asserts the persisted request-log effort for an item-driven change.
- Not affected: upstream wire shape, model catalog, dashboard schemas,
  migrations, settings.
