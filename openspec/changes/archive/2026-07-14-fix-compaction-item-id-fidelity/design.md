## Context

The compact endpoint can return a remote-compaction-v2 item such as `{"id":"cmp_...","type":"compaction_summary","encrypted_content":"..."}`. Codex-compatible normalization converts that record to `type="compaction"`, but currently rebuilds it from only `type` and `encrypted_content`. The encrypted payload remains bound to the discarded upstream ID, while the client later assigns and replays a different ID.

## Goals / Non-Goals

**Goals:**

- Preserve the authoritative non-empty upstream compaction item ID wherever the encrypted item is normalized or streamed.
- Keep the item identical between `response.output_item.done.item` and `response.completed.response.output[0]`.
- Cover both explicit upstream `output` items and `compaction_summary` fallback payloads.

**Non-Goals:**

- Decrypt, inspect, or rewrite encrypted compaction content.
- Generate a compaction item ID when upstream omits one.
- Change account-affinity, compact routing, or OpenAI-style compact responses.

## Decisions

1. `_compact_response_output_item` will copy a non-empty string `id` from the selected upstream item into the normalized `compaction` record. The synthetic stream already reuses that normalized mapping in both events, so preserving the ID at this boundary keeps all downstream representations consistent.

2. Missing or invalid IDs retain the existing no-ID behavior. Synthesizing an ID is rejected because a generated value cannot satisfy ciphertext that is bound to an upstream item ID.

3. The normalizer will continue selecting only the compact contract fields (`id`, `type`, and `encrypted_content`). Forwarding all upstream summary fields is rejected because it would broaden the Codex-facing response contract without evidence that those fields are accepted.

## Risks / Trade-offs

- [Risk] An upstream response without an exposed item ID cannot be repaired locally. -> Mitigation: preserve existing behavior for that legacy shape and test the authoritative v2 output shape that supplies an ID.
- [Risk] A malformed whitespace-only ID could still cause an invalid replay. -> Mitigation: only forward non-empty IDs after trimming for the validity check, while preserving the original non-empty value byte-for-byte.
