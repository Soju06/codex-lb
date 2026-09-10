# Context

The native-client definition already used for egress fingerprint preservation
is the boundary for this change. A request is native only when its inbound
`User-Agent` or `originator` identifies a first-party Codex client; continuity
headers alone are not sufficient.

This keeps the two contracts distinct. Native Codex already implements its own
WebSocket-to-HTTP fallback and interprets an abruptly ended SSE response as a
transport failure, so codex-lb should not promote that fallback or manufacture
a terminal frame. OpenAI SDK and other clients continue to receive codex-lb's
stable terminal-error envelope.

`Retry-After` is copied only as a bounded, CR/LF-free field value. It is not a
general upstream-header passthrough. The analyzer compares its normalized
value separately from payload semantics because retry timing is client-visible
failure behavior.
