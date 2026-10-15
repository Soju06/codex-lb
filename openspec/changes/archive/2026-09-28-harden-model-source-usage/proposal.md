## Why

Optional model-source telemetry can overflow database integers, accept booleans
as token counts, or lose reasoning usage. Network chunks can split UTF-8 or SSE
framing. These metadata problems should not interrupt valid response forwarding.
This is the independently reviewable source-usage change requested in #2444.

## What Changes

- Validate token counts and optional timing against the request-log integer range.
- Preserve reported reasoning usage through both request-log entrypoints.
- Decode fragmented UTF-8 incrementally and preserve complete SSE event framing.
- Keep response bytes, source selection and existing billing settlement unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: validate optional source telemetry and preserve
  reported reasoning usage without interrupting otherwise valid forwarding.

## Impact

Model-source forwarding/parser, its two logging entrypoints, and regression tests.
No migration, configuration, TPS threshold, dashboard or report-policy change.
