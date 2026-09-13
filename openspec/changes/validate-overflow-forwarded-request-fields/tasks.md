## 1. Egress neutralisation

- [x] 1.1 Add `neutralize_overflow_egress` and `overflow_opaque_value` to `app/modules/model_sources/projection.py`: remove `NEUTRALIZED_INCLUDE_VALUES` from `include` (dropping `include` once that empties it), replace each `NEUTRALIZED_IDENTIFIER_FIELDS` string with a namespace- and domain-scoped opaque token. Leave a non-string in either slot exactly as it is, so the projection never makes an unclassifiable value look classified.
- [x] 1.2 Add `overflow.overflow_source_wire_body` as the single overflow-only shaping step (`restore_client_store` then the neutralisation), and route both the body the verdict classifies (`_source_body`) and the body `api._source_responses_response` puts on the wire through it, scoped to the requesting API key.
- [x] 1.3 Leave direct source routing untouched: it still forwards the client's own `include`, `prompt_cache_key`, `user` and `safety_identifier`.

## 2. The closed field-value step

- [x] 2.1 Add `OVERFLOW_FIELD_CLASSIFICATION` to `replay_safety`, one class per field the view admits, pinned against `OVERFLOW_VIEW_FIELDS` in both directions.
- [x] 2.2 Add `_unportable_forwarded_field`: the closed `include` allowlist, the required opaque shape for the three rewritten identifiers, the closed `prompt_cache_retention` and `truncation` sets, and `metadata` as a string map naming no account-scoped reference. Never raises.
- [x] 2.3 Wire it into `responses_payload_is_provider_portable` after the configuration-class steps and before the history step, returning `not_portable_unknown_field` with the field (and the offending `include` entry) in `detail`.
- [x] 2.4 Correct the `_PORTABILITY_VIEW_ONLY_FIELDS` comment: `user`, `safety_identifier` and `prompt_cache_retention` are not "provider-neutral generation knobs".

## 3. Corpus grounding

- [x] 3.1 Make the fixture gate classify the body the overflow egress builds, not the raw stripped body, so the recorded verdict is the one production computes.
- [x] 3.2 Add the drift guard: a top-level field a committed body carries that production neither strips, nor rewrites, nor validates, nor records evidence for fails the suite **by name**, on the committed body and on the overflow egress body.
- [x] 3.3 Assert the three corrected declaration facts against the committed bytes (`tool_search` extras, `web_search` extras, the `additional_tools` `id`).
- [x] 3.4 Prove the neutralised value is what leaves, on the bytes a stub source received: two tenants sending the same `prompt_cache_key` and the same end-user identifier get two different proxy-minted tokens, and no `include` reaches the source.

## 4. Documentation

- [x] 4.1 Correct `tests/fixtures/codex_bodies/README.md`: two `tool_search` extras not one, the profile-dependent `web_search` extras, the `additional_tools` `id`, and the divergence table's environment-context row (0.154.0 moved it out of `instructions` into the first developer message).
- [x] 4.2 Correct the two `provenance.json` capture notes to match.
- [x] 4.3 Record `instructions` as deliberately `forwarded`, with the evidence, rather than leaving it unstated.

## 5. Open

- [ ] 5.1 Owner decision, unchanged and still separate: the false *rejections* the same sweep found (`tool_search.execution`/`parameters`, the real `web_search` declaration fields, input-item ids before the overflow decision). Nothing here relaxes an existing rejection.
