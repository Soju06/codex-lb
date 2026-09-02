# Context

Failure parity is not byte equality. codex-lb may translate an upstream socket
failure into a client-visible Responses failure, or a rejected WebSocket may
cause the client to retry over HTTP. The toolkit therefore keeps the existing
strict semantic comparison unchanged and adds an explicit observation layer:
HTTP status, `Retry-After`, terminal class, completeness, incomplete reason,
and a bounded network-error category.

Raw exception text is deliberately excluded because it may contain source or
proxy addresses, DNS names, and operating-system details. The controlled
origin scenarios are process configuration, not request-controlled behavior,
so an untrusted request cannot select a sleep or failure mode. The fixture
continues binding only to loopback unless the existing public-bind
acknowledgement is supplied.

Example: an upstream SSE stream that ends after `response.created` is recorded
on Path C as `transport_incomplete`. If codex-lb converts it to a downstream
`response.failed`, Path B is `failure_terminal`. The report shows that
translation explicitly while the ordinary strict comparison remains failed
because the lifecycle was not preserved exactly.
