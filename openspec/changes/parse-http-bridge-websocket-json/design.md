## Context and decisions

A WebSocket text message is a complete document, not an SSE field. Decode it
once with JSON semantics, or reuse its trusted native interpretation.
Keep the existing HTTP SSE line parser unchanged. Use the existing safe SSE
formatter when relaying a parsed multiline object.

This preserves ordinary event matching, account ownership, reservation
settlement, and error classification. No retry policy or configuration changes
are needed. Example: an indented `{"type":"error","status":400,"error":...}`
must settle the same request as its compact equivalent.

## Boundaries and risks

Malformed/non-object frames still have no classified response identity and are
dropped before entering the parsed-event relay, rather than forwarded as raw SSE.
For native-interpreted objects, check nested float values for finiteness without
re-parsing the message text. Opaque frames use the existing strict JSON callbacks.
Rejected data does not acquire lifecycle ownership; the existing acknowledgement
timeout remains in effect if no valid frame follows.
Existing tests equating multiline WebSocket JSON with a single SSE data line
must change, but actual SSE tests must retain their semantics.
Tests use synthetic errors and baseline route fixtures, not live payloads.
Lite request normalization and inline-image admission are separate changes.
