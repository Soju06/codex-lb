## Decisions

Recognize the close code at the existing bridge and direct-relay terminal
boundaries before retry selection. Reuse the shared request finalizer for
reservations, gates, leases and error delivery. Normalize payload_too_large to
HTTP 400 / invalid_request_error / param=input without overriding explicit
request-state error overrides.

Example: the only upstream account closes with 1009 before response.created.
The client gets the size error, not a second attempt ending in no_accounts.
A later smaller request can still select that account.

## Scope and caveats

No image-admission changes, per-image limits, receive-cap increases, native IPC
changes, parser fixes, Lite normalization, or new settings are included.
Other disconnects retain baseline behavior.

This handles a close 1009 exposed by the adapter. It does not infer message
size from arbitrary exception text. Some adapters can surface a locally sent
1009; this change does not add provenance or reclassify local overflow errors
that expose no close code. The neutral message describes a WebSocket size
limit, not a newly measured upstream cap.
