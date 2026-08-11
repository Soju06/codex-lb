## Why

The Codex compaction-trigger bridge currently emits only
`response.output_item.done` and `response.completed`. That abbreviated stream
worked with the first remote-compaction-v2 collector, but it is not a complete
Responses lifecycle and leaves stricter Codex clients, SDKs, and intermediaries
without the response and output-item creation events they use to initialize
stream state.

The live codex-lb runtime has already been operating with the complete event
sequence. Keeping that fix only in `site-packages` means a reinstall or upgrade
silently restores the abbreviated stream.

## What Changes

- Emit `response.created`, `response.output_item.added`,
  `response.output_item.done`, and `response.completed` in that order for a
  synthetic Codex compaction response.
- Number those events monotonically from zero.
- Preserve the authoritative compaction item ID, encrypted content, and
  terminal status across the done event and completed response, while exposing
  the same item as `in_progress` in the added event.
- Preserve a non-empty upstream compaction item status during normalization.
- Add focused regression coverage for the complete wire lifecycle and item
  fidelity.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Synthetic Codex compaction streams use a complete,
  ordered Responses lifecycle without changing standalone compact routing or
  public `/v1/responses/compact`.

## Impact

- Affected code: `app/modules/proxy/api.py`.
- Affected API surface: terminal compaction-trigger responses on
  `POST /backend-api/codex/responses`.
- No schema, dependency, configuration, dashboard, account-routing, or public
  OpenAI compact behavior changes.
