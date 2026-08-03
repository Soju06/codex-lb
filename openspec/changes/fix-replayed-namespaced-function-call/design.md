## Context

Responses request models intentionally retain unknown input-item fields so local proxy features can inspect metadata added by newer clients. Namespaced side-effect calls use `namespace` together with `call_id` for replay deduplication, but OpenAI's Responses request schema does not accept `namespace` on historical `input` tool calls. The same outbound field-stripping path is shared by normal and compact Responses requests.

## Goals / Non-Goals

**Goals:**

- Produce an upstream-compatible wire payload for replayed namespaced `function_call`, `custom_tool_call`, and `apply_patch_call` items.
- Keep namespace metadata available on the request model for local replay deduplication.
- Cover both normal and compact Responses serialization and the public proxy path.
- Leave client-provided top-level tool entries untouched.

**Non-Goals:**

- Changing namespaced dedupe identity or settlement behavior.
- Removing unknown fields from other input-item types.
- Canonicalizing or rewriting top-level tool definitions.
- Relaxing cross-account replay safety.

## Decisions

### Normalize only the copied outbound payload

The shared unsupported-field sanitizer will copy only affected input items and remove `namespace` when the item type is one of the recognized replayed tool-call types. Request model input remains unchanged, so internal consumers retain the namespace.

Stripping during model validation was rejected because it would erase local dedupe identity. Broad unknown-field filtering was rejected because it could silently remove future-compatible client data and expand the scope beyond issue #1450.

### Reuse the standard outbound sanitizer for compact requests

Compact serialization already delegates to the standard unsupported-field sanitizer before compact-specific trimming. Adding the compatibility normalization there keeps both wire paths consistent without duplicating behavior.

### Preserve local metadata during replay-safety classification

Account-neutral replay classification uses the same general request normalization but must retain namespace metadata so unknown owner-scoped call identity continues to fail closed. A dedicated replay-safety payload method skips only namespace removal; actual HTTP, WebSocket, compact, and model-source egress still removes it.

Configured OpenAI-compatible model sources preserve request fields that the Codex upstream path strips, so source egress reuses only the namespace sanitizer rather than the full Codex unsupported-field sanitizer.

### Prove behavior at serialization and public route boundaries

Unit tests will assert wire normalization, malformed-item handling, request-model preservation, and fail-closed replay classification. Integration tests will capture payloads forwarded by `/v1/responses`, a configured Responses model source, and the actual upstream WebSocket `response.create` frame.

## Risks / Trade-offs

- [Future upstream support for input-item namespace] The proxy will continue omitting the field → Scope the rewrite narrowly to historical tool-call input items and preserve it internally.
- [Accidental mutation of local request state] In-place item mutation could erase dedupe identity → Copy each changed item and replace only the outbound payload list.
- [Top-level namespace tool regression] A broad namespace scrub could alter tool definitions → Do not traverse `tools`; retain existing byte-preservation coverage.

## Migration Plan

No data migration or configuration change is required. Deploy the serialization fix normally; rollback restores the prior request serialization behavior.

## Open Questions

None.
