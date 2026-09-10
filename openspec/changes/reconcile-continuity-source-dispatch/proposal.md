## Why

PR #1905 conflicts with upstream source selection and compact resilience binding.
The resolved composition must preserve the accepted continuity rules and the
new source-dispatch lifecycle. The original ownership change was archived by
upstream, so this follow-up records integration work without rewriting it.

## What Changes

- Retain the typed ownership resolver and boolean wrapper alongside the new
  designated overflow selector.
- Keep compact's moved owner checks and settlement handling, and bind the
  dashboard resilience snapshot at its moved location.
- Verify source dispatch is unreachable for bound subscription continuations
  and remains fully owned for source-routed requests.
- Align one existing test with dashboard-only transport settings.
- Complete the existing synthesized-marker compatibility rule for direct
  WebSocket reconnects without a previous-response identifier.
- Complete that same rule for marker-only HTTP Responses requests on both
  raw-stream and session-bridge paths, preserving independent owners and sources.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This composition preserves existing Responses, source-dispatch, and
dashboard-resilience requirements. `skip_specs: true` avoids inventing a new
policy for a merge resolution.

## Impact

Source-selection and compact-service conflict resolution, integration tests,
and verification records. No new settings, schema, endpoint, overflow routing,
or replay policy. Inherited upstream runtime changes remain intact.
