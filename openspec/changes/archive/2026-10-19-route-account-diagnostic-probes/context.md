# Context

Diagnostics carry the same account credentials as normal traffic. Their routing must therefore use the local account ID rather than an upstream workspace ID shared by several seats.

The existing account resolver chooses an active binding before the default policy. A broken active binding is an error, while an unbound account follows the existing global/direct policy. The routed client handles proxy authentication and transport classification. Force Probe reads response headers only and releases the response without waiting for the SSE body.

Quota warmup retains its execution claim, reservation, and cancellation handling. Its existing request logs record resolved routing before dispatch and the actual endpoint when the transport returns response metadata. A resolved endpoint on a failed request does not prove that a connection completed. Force Probe continues to report its existing status snapshot and audit event; this change does not add a separate request-log lifecycle.
