## Why

With `CODEX_LB_METRICS_ENABLED=true`, `app/main.py` starts the Prometheus
server with `uvicorn.Config(metrics_app, ..., log_level="warning")`. uvicorn
applies a `Config`'s logging process-wide: it runs `dictConfig` with its stock
`LOGGING_CONFIG` and sets `uvicorn.error` / `uvicorn.access` to the given
level. Starting the metrics server therefore:

- raises `uvicorn.access` to WARNING, so the main server stops writing access
  lines;
- rebinds the `uvicorn` and `uvicorn.access` loggers to uvicorn's own
  formatters, which skip codex-lb's redaction, so `uvicorn.error` records
  written after that point are not redacted.

## What Changes

- The metrics server's `uvicorn.Config` passes `log_config=None` and
  `log_level=None`, so it leaves the logging that the server command set up
  alone.
- Metrics scrapes (`GET /metrics` on the metrics port) now appear in the
  access log, because both servers share the process-wide `uvicorn.access`
  logger. uvicorn's per-server `access_log=False` would clear that shared
  logger's handlers for the main server too, so it is not used here.

## Impact

- `app/main.py`: metrics server config only.
- Operators with metrics enabled get their access log back, now with one line
  per scrape on the metrics port.
- No settings, API, or schema changes.
