## Tasks

- [x] Add an `only_disabled` enabled-state filter to
      `find_chat_source_for_model` / `find_responses_source_for_model`, sharing
      one predicate so the routable set and its complement cannot drift.
- [x] Thread `only_disabled` through `select_responses_model_source` and
      `_select_chat_model_source`, so the refusal lookup reuses the selection
      rules rather than restating them.
- [x] Add `_disabled_model_source_denial` and call it from
      `/v1/chat/completions`, `/v1/responses`, and
      `/backend-api/codex/responses` after the ordinary lookup misses.
- [x] On the chat route, run the refusal before the usage reservation is taken
      so a refusal strands nothing.
- [x] Keep the existing source-routing exclusions intact: file-pinned
      Responses requests and terminal compaction triggers skip the refusal and
      continue to subscription routing.
- [x] Recognize disabled-source ownership in the WebSocket source-ownership
      guard (`responses_model_is_source_owned`), so both the connect-time and
      the socket-reuse guard bounce such turns to the HTTP transport where the
      refusal fires, and cover both guard sites with regression tests.
- [x] Add the spec delta for `responses-api-compat`.
- [x] Cover the refusal (disabled source, disabled source model, all three
      routes) and the negative controls (unknown model, subscription slug
      shadowed by a disabled source) with integration tests.
