# Context

The native helper already distinguishes receive failure phases, but the Python
WebSocket adapter drops that metadata before terminal request logging. For
example, `consumer_backpressure` becomes an undifferentiated `stream_incomplete`.
An allowlist carries only bounded internal classifications, never raw helper
messages or unrecognized values. Existing request classifications take precedence.

Propagation happens after the transparent-replay decision so a recovered attempt
cannot contaminate a later successful request. This change adds no retry, routing,
queue, account-health, schema, or configuration policy. Public errors stay intact.
It improves diagnosis; it does not claim to repair the underlying disconnect.

The existing large WebSocket mixin remains the finalization owner. A seven-line
propagation at that boundary avoids introducing a competing owner or broad
refactoring into this narrow observability change.

# Verification

The seven adapter cases and route cases first failed on missing phase metadata.
All 13 new cases then passed; 99 focused adapter/native/receive/liveness/terminal
failure cases passed together. Ruff formatting/checks and a read-only review
passed. Route coverage verifies request-log arguments, rather than a database
insertion. Unknown phases, sanitized public messages, and stronger pre-existing
classifications are covered.
