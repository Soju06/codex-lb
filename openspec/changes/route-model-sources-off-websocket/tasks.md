## Tasks

- [x] Extract `select_responses_model_source` / `allowed_source_ids_for_api_key`
      into `app/modules/model_sources/selection.py`; delegate from
      `app/modules/proxy/api.py`.
- [x] Add `responses_model_is_source_owned` for transport-level checks.
- [x] Guard `_select_websocket_connect_account` so source-owned models fail the
      WebSocket connect instead of selecting a subscription account.
- [x] Return `503` so the Codex client falls back to the HTTP transport.
- [x] Add spec delta for `responses-api-compat`.
