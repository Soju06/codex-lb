## 1. Catalog metadata

- [x] 1.1 Derive source-model reasoning levels, default level, and summary
      support from `raw_metadata_json`.
- [x] 1.2 Restrict the declared default to one of the advertised efforts.

## 2. Effort delivery

- [x] 2.1 Apply the unsupported-effort rewrite unconditionally at enforcement
      time and restore it at the source-routing branch, gated on the effort
      being declared for that source model. Route membership is not inferred
      from the model registry.
- [x] 2.2 Report the replaced effort from the normalizer and thread it through
      enforcement. Paths whose enforced Responses payload only ever reaches a
      subscription (WebSocket, stream, collect, compact, and chat -- whose own
      source branch forwards the untouched original chat payload) discard it, so
      the workaround still applies there.
- [x] 2.3 Report only fallback rewrites, so the `ultra` -> `max` wire alias
      survives source routing, and restore the normalized effort form.
- [x] 2.4 Normalize and deduplicate declared efforts, validating shape rather
      than membership of a fixed vocabulary, so operator-declared `none` and
      other provider-specific efforts survive.
- [x] 2.5 Gate catalog derivation, the declared-levels accessor and the
      chat-path opt-in on the `supports_reasoning` switch, so the Codex
      catalog, `/v1/models`, the chat sanitizer and the restore agree.

## 3. Verification

- [x] 3.1 Unit coverage for slug lists, object lists, malformed entries, an
      out-of-range default, and the no-metadata default.
- [x] 3.2 Manual end-to-end check that `/backend-api/codex/models` advertises the
      declared efforts and that forwarding behavior is unchanged.
- [x] 3.3 Unit coverage for the restore matrix (declared, undeclared, enforced),
      the never-restored `ultra` alias, and the normalized restored form.
- [x] 3.4 Integration coverage that a source declaring `minimal` receives it,
      via both `/v1/responses` and the codex-native `/backend-api/codex/responses`
      route. Mutation-checked per call site: dropping the restore call, or the
      threading at either route, fails the corresponding test. One test per route
      is required -- the codex-native threading is invisible to the `/v1` test.
- [ ] 3.5 The WebSocket scenario is verified by inspection only: the WebSocket
      service tree contains no model-source references, so there is no restore to
      suppress. Left unchecked rather than claimed as tested.

