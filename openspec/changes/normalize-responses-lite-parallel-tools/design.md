## Context and decisions

The shared Lite finalizer already runs at HTTP, bridge, direct WebSocket,
compact, and fallback serialization boundaries. Extend that boundary with
`parallel_tool_calls=false` rather than add a global parameter override or
independent Lite detectors.

Example: an `additional_tools` request with `parallel_tool_calls=true` keeps
its tools and input but sends false after Lite classification. A trusted
marker-only continuation receives the same normalization; an untrusted marker
must still be stripped before serialization.

## Boundaries and risks

Lite requests use serialized tool calls because the backend requires them.
Non-Lite requests keep their setting. Normalization is idempotent, does not
alter context/history/cache keys, and introduces no flag or dependency.
Tests inspect final egress through existing routes and builders, independently
of WebSocket error parsing and image admission.
