## Context

The native request model owns the include allowlist. `V1ResponsesRequest` delegates validation to that model during conversion, and HTTP and WebSocket entry points use the same contract. Output item dictionaries already retain opaque fields.

## Goals / Non-Goals

**Goals:** Accept the encrypted-output selector and protect field preservation with request, transport, SDK and replay-classification regressions.

**Non-Goals:** Widening the allowlist to arbitrary strings, changing account routing, decrypting output, altering composer predictions, changing compaction normalization, or deploying a new image.

## Decisions

- Add one value to the existing allowlist. Removing the allowlist would weaken unrelated validation; treating the value as an alias for reasoning would lose the client's request for encrypted non-reasoning output.
- Keep the existing transport and serializer implementation. Test exact opaque string values, item IDs, message phase and encrypted function arguments through output and subsequent input.
- Use shared synthetic output data in ordinary tests. No captured conversations, credentials, environment-dependent patch fixtures or live upstream calls are needed.
- Preserve the existing replay-safety baseline: message and tool-call ciphertext cannot become account-neutral merely because their item IDs were removed.

## Risks / Trade-offs

- Mocked ciphertext proves field preservation, not provider-side cryptographic validity. Pin the client source in context and keep that evidence boundary explicit.
- Native SSE can leave terminal `output` empty after item events; tests preserve that contract while checking public SSE/JSON backfill.
- Other local fixes can share the same source file. Apply the single-line diff rather than replacing the file wholesale.
