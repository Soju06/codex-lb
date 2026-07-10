# Tasks

- [x] 1. Add field-aware Responses serialization that omits only an unset top-level `tools` field.
- [x] 2. Preserve omitted-tools provenance through V1 conversion, owner forwarding, and model-source Responses egress.
- [x] 3. Remove tool canonicalization from the outbound wire and retain canonical-copy hashing for observability only.
- [x] 4. Add unit regressions for omitted versus explicit tools, explicit tool ordering, V1 conversion, and stable non-mutating hashes.
- [x] 5. Add HTTP, websocket, HTTP-bridge, and owner-forward product-path regressions.
- [x] 6. Run focused tests, lint, type checks, and strict OpenSpec validation.
