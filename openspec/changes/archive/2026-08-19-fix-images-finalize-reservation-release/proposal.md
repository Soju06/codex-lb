## Why

Image generation and edit routes reserve limited API-key quota but deliberately
exclude that reservation from the internal Responses stream settlement path.
Their image-specific finalizer currently logs and abandons the reservation when
persistence fails, leaving quota charged until stale cleanup and leaving
graceful persistence drain unaware of the unresolved work.

## What Changes

- Transfer image reservation settlement to the existing tracked,
  cancellation-safe stream settlement machinery while preserving captured
  `tool_usage.image_gen` tokens as the authoritative usage source.
- Preserve successful public Images JSON and SSE responses when settlement
  fails or is cancelled.
- Transfer failed or cancelled finalization to the existing tracked,
  retrying release fallback so persistence drain remains aware of unresolved
  ownership.
- Keep the internal Responses stream reservation-free to prevent duplicate
  settlement across image and standard response paths.

## Capabilities

### Modified Capabilities

- `images-api-compat`: require successful image generation and edit paths to
  retain tracked reservation ownership through finalization or fallback release.

## Impact

- Affects the image generation/edit settlement handoff in
  `app/modules/proxy/api.py` and the reusable API-key settlement seam in
  `app/modules/proxy/_service/api_key_usage.py`.
- Adds event-driven integration coverage for finalization failure,
  cancellation, release retry, persistence drain, and all four image response
  modes.
- Does not change database schema, API-key repository transitions, retry
  constants, scheduler policy, external response schemas, or settings.
