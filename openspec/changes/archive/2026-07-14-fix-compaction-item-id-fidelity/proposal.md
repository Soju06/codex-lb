## Why

Codex compaction-trigger responses currently discard the upstream compaction item's `id` while preserving its `encrypted_content`. The client assigns a replacement `cmp_*` ID, so the next replay fails upstream verification because the ciphertext is cryptographically bound to the original item ID.

## What Changes

- Preserve a non-empty upstream compaction item `id` when normalizing remote compaction-v2 output.
- Emit the same ID with the encrypted compaction item in both synthetic SSE events.
- Add product-path regression coverage proving the ID survives compact normalization and compaction-trigger streaming unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require encrypted compaction output to retain its authoritative upstream item ID across Codex-compatible normalization and synthetic streaming.

## Impact

- Affected code: `app/modules/proxy/api.py` compaction output normalization.
- Affected API surfaces: `POST /backend-api/codex/responses` compaction triggers and Codex-affinity `POST /backend-api/codex/responses/compact`.
- No schema, dependency, configuration, or OpenAI-style `/v1/responses/compact` behavior changes.
