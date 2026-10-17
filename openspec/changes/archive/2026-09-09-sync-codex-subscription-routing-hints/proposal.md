# Restore subscription Codex routing hints

Codex 0.153.4 builds upstream routing hints from the final model and service
tier. Main currently discards incoming hints without synthesizing replacements.
Port the focused routing-hint changes from minpeter's fork and complete the
missing compaction path found during source comparison.

Subscription Responses HTTP, transient and persistent WebSocket handshakes,
HTTP fallback, and compaction will synthesize hints from trusted request state.
Inbound hints remain untrusted. Custom providers and non-subscription transports
retain their existing behavior. No prompt, tier entitlement, retry, pricing, or
quota policy changes are included.

Native OpenAI controls on the tested account also returned actual tier default.
This change restores request parity; it does not guarantee an upstream Fast grant.
