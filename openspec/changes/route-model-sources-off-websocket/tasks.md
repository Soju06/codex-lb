## Tasks

- [x] Extract `select_responses_model_source` / `allowed_source_ids_for_api_key`
      into `app/modules/model_sources/selection.py`; delegate from
      `app/modules/proxy/api.py`.
- [x] Add `responses_model_is_source_owned` for transport-level checks.
- [x] Guard `_select_websocket_connect_account` so source-owned models fail the
      WebSocket connect instead of selecting a subscription account.
- [x] Return `503` so the Codex client falls back to the HTTP transport.
- [x] Consider the API key's `enforced_model` in the guard, matching the
      candidate list the HTTP handlers build.
- [x] Add spec delta for `responses-api-compat`.
- [x] Cover the guard and the source-owned check with unit and integration
      tests, including the `require_streaming` edge and the enforced-model case.
- [x] Apply the guard to every prepared `response.create` so socket reuse cannot
      forward a source-owned model to the open subscription upstream (Codex P2).
