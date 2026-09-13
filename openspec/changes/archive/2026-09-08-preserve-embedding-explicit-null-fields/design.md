## Context

`V1EmbeddingsRequest` validates `model` and `input` while allowing arbitrary compatible extras. Pydantic retains both extra values and their presence, but `_source_embeddings_response` currently serializes with `exclude_none=True`, which converts every explicit null into omission before the forwarding adapter receives the payload. The forwarding adapter sends its input dictionary unchanged.

## Goals / Non-Goals

**Goals:**

- Preserve the client's distinction between an omitted embedding extra and an explicitly supplied null.
- Keep non-null extras, source routing, reservation settlement, and request-log metadata unchanged.
- Prove behavior through the real ASGI route and an inert captured source.

**Non-Goals:**

- Change serialization for any endpoint other than source-routed `/v1/embeddings`.
- Change model-source selection, authentication, usage accounting, logging, or error handling.
- Add a compatibility shim, setting, dependency, migration, or dashboard behavior.

## Decisions

- Serialize the embeddings request with Pydantic presence semantics (`exclude_unset=True`). This omits values absent from the inbound model while retaining explicit null and non-null values. Using a manual field-set reconstruction would duplicate behavior Pydantic already guarantees; removing all exclusion would be broader than the required presence contract.
- Add three route-level source-capture cases: explicit null, omitted extras, and non-null extras. The existing settled limited-key case remains the non-null control and continues asserting reservation-derived token accounting and request-log source metadata.
- Sync the stable requirement into `openspec/specs/model-source-routing/spec.md` because the owning capability was introduced by the still-active `add-model-source-embeddings` change and has not yet been materialized under main specs.

## Risks / Trade-offs

- **[Risk] A serializer change could accidentally emit absent optional fields.** → Mitigation: captured-source omitted control asserts binary key absence.
- **[Risk] Payload correctness coverage could hide accounting regressions.** → Mitigation: retain and extend the existing limited-key settlement and request-log assertions.
- **[Risk] A shared serializer change could affect unrelated routes.** → Mitigation: change only the single embeddings outbound serialization call.
