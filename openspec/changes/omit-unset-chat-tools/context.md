Responses Lite and `/v1/responses` already drop synthesized top-level
`tools` via `model_fields_set` (issue #1184). Chat conversion was left out
of that change because `_normalize_chat_tools` always constructed a list,
and source-routed chat already popped empty arrays.

The Codex-backend chat path still goes through `to_responses_request()`
and then `to_payload()`. An omitted chat `tools` field becomes
`"tools": []` on the upstream Responses body. Models that reject an
explicit empty tools param can 400 the same way Lite did.

Example: `POST /v1/chat/completions` with
`{"model":"gpt-5.2","messages":[{"role":"user","content":"hi"}]}`.
The mapped payload must not contain `tools`. The same request with
`"tools": []` must still forward `[]`.
