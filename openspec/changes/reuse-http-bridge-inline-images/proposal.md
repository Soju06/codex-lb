# Reuse bridge sessions for inline images

## Why

Any image in retained history currently disables reusable HTTP bridge sessions. Switching the standalone transport to WebSocket in #2363 did not restore connection reuse.

## What Changes

Allow otherwise eligible inline images within the frame budget through the existing bridge. Preserve external-URL, oversized-payload and image-generation exclusions. Cover physical connection reuse, pre-acknowledgement errors, silent-upstream no-replay and cancellation cleanup.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: inline-image bridge eligibility and lifecycle safety.

## Impact

One routing condition and an image-specific no-replay safeguard; no new settings, dependencies, schema or wire format. Provider cache hits are not guaranteed.

## Review holds

Current-turn image routing needs a maintainer decision. An authentic invalid-image upstream frame from #903 is still unavailable; synthetic error tests are not evidence of that provider behavior. Do not archive until these review items are resolved.
