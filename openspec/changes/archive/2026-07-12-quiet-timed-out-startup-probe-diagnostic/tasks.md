# Tasks

- [x] Replace `wait_for` + `shield` with `asyncio.wait` in `_probe_stream_startup_error` and `_probe_chat_stream_startup_error`
- [x] Retrieve the probe task's exception via a done-callback for the abandoned-stream case
- [x] Cancel the still-running probe task when the wrapping stream is closed early
- [x] Add regression coverage in `tests/unit/test_responses_streaming_timeout_hardening.py`
- [x] Document the proxy-runtime-observability requirement delta (proposal + ADDED requirement with GIVEN/WHEN/THEN scenarios)
- [x] Run `uv run --frozen ruff check .` and `uv run --frozen ruff format --check .`
- [x] Run `uv run --frozen pytest tests/unit/test_responses_streaming_timeout_hardening.py`
