# Context

Refs #1934. The issue reports two independent parser incompatibilities:
string-valued `response.instructions`, and an unrecognized
`responsesapi.websocket_timing` event. This change addresses only the latter.

The existing public contract already specifies the `response.*` and `error`
families. Filtering by those families avoids adding a vendor-name registry and
continues accepting future `response.*` events. It does not validate every
event subtype or change malformed-payload handling.

For example, upstream `response.created`, `response.output_text.delta`,
`responsesapi.websocket_timing`, `response.completed` becomes the same sequence
without the timing diagnostic on the public surface. A native Codex request
continues receiving the diagnostic. OpenAI-shaped requests to the backend route
already enable public contract enforcement and therefore also filter it.

Regression tests inject upstream SSE into the real application route and stream
normalizer. They do not exercise IntelliJ, Koog, or a live ChatGPT account and
cannot establish complete IntelliJ compatibility.
