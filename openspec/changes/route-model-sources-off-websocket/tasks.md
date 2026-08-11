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
- [x] Fail open to subscription selection when source resolution raises, so a
      database failure cannot end the session or strand a usage reservation.
- [x] Evaluate the connect guard once per connect series instead of per failover
      attempt, and judge it with the per-request api key rather than the
      session key.
- [x] Gate the reuse guard on a live upstream reader so a socket that died
      between turns reconnects into the 503 fallback instead of receiving a
      terminal error.
- [x] Finalize the request-log row on the reuse-guard path.

