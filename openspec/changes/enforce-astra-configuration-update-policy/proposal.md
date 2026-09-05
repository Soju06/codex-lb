## Why

Subscription Astra clients persist `configuration_update` items across
response and conversation anchors. API-key allowed/enforced reasoning
controls currently inspect only request-level `reasoning.effort`, so an
in-input update can bypass the key. Codex-rs already emits these items.

## What Changes

- Apply allowed and enforced reasoning controls to `configuration_update`
  items on subscription Astra requests.
- Reject unsupported Astra update shapes before upstream work.
- On anchored continuations with a restricted key, prepend an allowed
  leading update so inherited effort cannot bypass policy.
- Keep client-plane Ultra distinct from Max during policy checks; map
  Ultra to Max only at subscription wire serialization.
- Do not apply subscription Astra schema restrictions to externally
  configured model sources that share the model ID.
- Leave catalog bootstrap to #2085. Do not move
  `_REASONING_EFFORT_WIRE_ALIASES` / `resolve_wire_reasoning_effort`.
- Out of scope: async tool continuity, WebSocket `response.steer`,
  reservation `FOR UPDATE` on every finalize/release.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: reasoning controls cover `configuration_update` items and
  anchored Astra continuations.
- `responses-api-compat`: subscription Astra preserves compatible
  configuration-update history and rejects incompatible combinations.

## Impact

- Code: `app/modules/proxy/request_policy.py`, `app/core/openai/requests.py`,
  `app/modules/proxy/api.py`, HTTP-bridge prepare path, exception mapping.
- Tests: `tests/unit/test_astra_request_policy.py`,
  `tests/unit/test_astra_inherited_policy.py`,
  `tests/integration/test_astra_request_policy.py`,
  `tests/integration/test_astra_inherited_policy.py`,
  `tests/integration/test_astra_source_policy.py`.
- No settings, schema, migration, or dashboard changes.
