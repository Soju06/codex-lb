## 1. Implementation

- [x] 1.1 In `app/main.py`, create the metrics server's `uvicorn.Config` with `log_config=None, log_level=None`. Verify with 2.1.

## 2. Tests

- [x] 2.1 `tests/integration/test_metrics_server_logging.py`: start the real server command with metrics enabled, wait for `/metrics`, then request `/health` with a credentialed URL in the query. The access line appears, the credential is redacted, and the scrape is logged. Fails on the previous code (no access line), passes now.

## 3. Validation

- [x] 3.1 Run `npx --yes @fission-ai/openspec@1.11.0 validate fix-metrics-server-logging-config --strict`, `make lint`, and `uv run ty check`.
