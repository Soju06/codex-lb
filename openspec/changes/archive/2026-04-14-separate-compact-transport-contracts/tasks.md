## 1. Spec

- [x] 1.1 Add Responses compatibility requirements that compact success returns a canonical opaque context window
- [x] 1.2 Add Responses compatibility requirements that compact transport fails closed and does not surrogate through `/codex/responses`
- [x] 1.3 Sync Responses compatibility context to describe the dedicated compact contract boundary
- [x] 1.4 Validate OpenSpec specs after the spec updates

## 2. Tests

- [x] 2.1 Add unit coverage that direct compact success preserves retained items and opaque compaction items without rewriting
- [x] 2.2 Add regression coverage that a compact transport failure does not trigger any surrogate `/codex/responses` request
- [x] 2.3 Add integration coverage that compact output is returned unchanged and can be fed back into the next `/responses` request without proxy-side pruning
- [x] 2.4 Add unit/integration coverage that safe compact transport failures retry only against `/codex/responses/compact`
- [x] 2.5 Add tracing/logging coverage that compact requests report direct compact path, retry attempt, failure phase, affinity source, and no surrogate fallback

## 3. Implementation

- [x] 3.1 Extract a dedicated compact transport boundary separate from normal Responses session transport
- [x] 3.2 Introduce a dedicated compact response payload type or equivalent opaque pass-through representation
- [x] 3.3 Remove any silent compact surrogate path that calls `/codex/responses` or reconstructs compact windows from SSE
- [x] 3.4 Add bounded same-contract retry for safe compact transport phases and preserve fail-closed behavior for ambiguous failures
- [x] 3.5 Preserve existing compact routing, refresh/retry, request logging, and API key settlement on the new boundary
- [x] 3.6 Add compact-specific tracing fields for endpoint, payload object type, retry attempt, failure phase, affinity source, and fallback suppression
