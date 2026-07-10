# Design

## Preserve field presence

Use Pydantic's `model_fields_set` as the provenance signal. A
`ResponsesRequest` forwarding dump removes `tools` only when that field was not
present in the validated input. An explicit `tools: []` therefore remains
explicit, while the model can keep its list default for internal code that
iterates `request.tools`.

Do not switch the model to `exclude_unset=True`: other defaults, especially
`store: false` and `include: []`, are intentional parts of the existing
contract. The change is deliberately limited to `tools`.

`V1ResponsesRequest.to_responses_request()` must remove its default tools list
before constructing the native request when the V1 input omitted the field.
Otherwise websocket normalization and `/v1/responses` conversion would mark the
synthesized list as explicit and defeat the native serializer's provenance
check.

Multi-instance owner forwarding uses a field-aware JSON body so the receiving
instance reconstructs the same unset state. Its existing signature remains
unchanged for rolling compatibility: the sender hashes the model dump with the
default list, and the receiver reconstructs the same default before verifying
the signature. Consequently, omitted tools and an explicit empty list retain
the same legacy signature digest. This equivalence is accepted inside the
authenticated internal owner-forward boundary so mixed-version deployments
continue to interoperate while the forwarded JSON body preserves field
presence.

## Preserve explicit tools

Remove tool canonicalization from the shared upstream wire sanitizer. Pydantic
keeps the list and mapping insertion order of JSON-valued tool definitions, so
the forwarded payload retains the client-provided order and structure. Existing
route-specific compatibility behavior remains in place; for example,
model-source egress may filter unsupported tool types, but it does not
canonicalize or reorder the surviving definitions.

The request-shape `tools_hash` remains stable across semantically equivalent
ordering by canonicalizing a detached copy before hashing. This hash is
observability-only and never replaces the outbound tools value.

## Failure boundaries

The Responses compact endpoint keeps its explicit field-removal policy.
`tool_choice` and `parallel_tool_calls` already default to `None` and are
omitted by `exclude_none=True`, so they need no provenance-specific rewrite.
