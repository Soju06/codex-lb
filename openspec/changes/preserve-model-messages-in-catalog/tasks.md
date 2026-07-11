## Tasks

- [x] Remove `_FILTERED_FIELDS` from `app/core/clients/model_fetcher.py`;
      replace the filter comprehension with `raw = dict(data)`.
- [x] Add `model_messages` to the `_CodexResponse` mock in
      `tests/unit/test_model_fetcher.py` and assert it survives in
      `UpstreamModel.raw`.
- [x] Extend `test_backend_codex_models_entry_has_upstream_fields` in
      `tests/integration/test_v1_models.py` to include `model_messages` in the
      `raw` fixture and assert it appears in the `/backend-api/codex/models`
      response.
- [x] Run `openspec validate preserve-model-messages-in-catalog --strict`.
- [x] Run `uv run ruff check app/core/clients/model_fetcher.py`.
- [x] Run focused tests: `tests/unit/test_model_fetcher.py` and
      `tests/integration/test_v1_models.py`.
