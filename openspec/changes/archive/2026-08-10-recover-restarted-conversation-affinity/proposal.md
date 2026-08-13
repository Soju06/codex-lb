## Why

Codex conversation restart can resend a self-contained thread under the same process-session identifier after the durable owner exhausts quota. A legacy hard `codex_session` row currently keeps that restarted request pinned to the unavailable owner, so it fails even while another account can serve it.

## What Changes

- Recognize the existing Codex goal-continuation context as an explicit restart signal.
- Permit a restart-shaped, self-contained request to retire its unavailable legacy hard owner and select another account.
- Classify accepted compatibility and transport request forms through the same canonical replay-safety body.
- Ensure live/durable HTTP bridge reuse and stale account snapshots cannot bypass or undo guarded owner retirement.
- Keep detached pending work restart-blocking and let an idle predecessor release a cap-constrained replacement slot.
- Keep ordinary incremental, conversation-bound, file-pinned, and unresolved tool-state requests fail-closed on their required owner.
- Make retirement compare-and-set so a concurrent owner change cannot be deleted.
- Scope retirement to process-session interpretation so an equal raw explicit turn state retains its owner.
- Derive mutation authority before model and service-tier replacement eligibility.
- Cover the public Codex Responses route and subsequent continuity on the replacement owner.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: Define the proof-gated exception that lets an explicit self-contained Codex restart abandon an unavailable legacy hard owner.

## Impact

- Account selection and sticky-session persistence in the proxy module.
- One nullable sticky-session column records source-qualified abandonment; existing tombstones remain global without backfill.
- Responses request classification shared by HTTP and WebSocket transports.
- HTTP Responses bridge reuse, forwarding, and replacement-session lifecycle.
- Routed regression coverage for `/backend-api/codex/responses`.
- No configuration or public error-envelope change.
