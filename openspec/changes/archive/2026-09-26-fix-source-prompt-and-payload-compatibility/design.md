## Context

See proposal.md for the reproduced failures. Source requests use a field-preserving serialization; subscription requests apply additional cleanup. Ownership is already shared through active and historical fingerprint records in the database. Production has multiple backends, while source load and cooldown state remain replica-local.

## Goals / Non-Goals

**Goals:** close the four reproduced gaps without changing admission, settlement, or retry ownership. Exercise both public Responses paths and independent application instances using shared storage.

**Non-Goals:** new pooling settings, global counters, migrations, prompt-template discovery, subscription prompt-template support, or deployment.

## Decisions

- Add prompt IDs to the existing scoped reference vocabulary. Successful request publication already persists input keys before completion; reuse it instead of adding a prompt cache or schema. Unknown IDs fail closed in pools, original external IDs retain single-source bootstrap, and override-introduced IDs require prior ownership even with one source.
- Inspect the documented direct `prompt.variables` values for `input_file` and `input_image` references. Reject file-bearing effective source payloads before admission and reservation, just like hosted container files. Do not redirect override bodies to subscription accounts or scan arbitrary function schemas/text for apparent IDs. Existing original input-file routing stays unchanged.
- Extract original keys with `model_dump_for_forwarding()` both in balanced dispatch and source-miss checks. Use the same representation as source shaping, without applying source overrides. This avoids treating subscription-only cleanup as a client/source difference.
- Classify integer `top_logprobs` from 0 through 20 as neutral for direct sources only; exclude booleans and malformed/out-of-range values. Preserve the wire field and the narrower subscription replay classifier.

## Risks / Trade-offs

- Old releases did not record prompt IDs → unknown prompt-only state in a pool remains denied. A successful single-source request can establish ownership; never infer prompt ownership from an unrelated response anchor.
- Rolling back loses these guards → apply the fix to all backends through the existing HA rollout when deployment is requested. No runtime state mutation is needed.
- Prompt variables can also contain ordinary text or inline/URL content → accept reference-free values and reject only supported file reference shapes. Malformed metadata must not crash candidate selection.

## Migration Plan

No schema change or backfill is required. Existing ownership tables store fingerprints of the new reference kind. Verify with SQLite and isolated shared PostgreSQL, then archive only after regression tests and independent review pass.
