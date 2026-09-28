## Decision

Derive namespace support in `source_model_supported_tool_types`, where search
and explicit experimental tool opt-ins are already interpreted. The existing
Responses filter then preserves the original namespace object and supported
tool choices. No new serializer or request-rewriting layer is needed.

A nonblank string declares the capability, including a future version name.
This follows the issue's proposed contract rather than imposing a local version
allowlist. Whether to accept that contract remains a maintainer decision.

## Boundaries

The change applies to source-bound HTTP Responses requests. Input replay
normalization, subscription routing, catalog instruction projection and
transport selection keep their existing behavior. See [context.md](context.md)
for examples, reporter attribution and operational constraints.

## Acceptance

The route tests verify payload preservation against a local recording upstream.
The live smoke test verifies a real parent/child session separately. Main-spec
synchronization and archival follow maintainer agreement on the modified
contract; the active delta records the proposal until then.
