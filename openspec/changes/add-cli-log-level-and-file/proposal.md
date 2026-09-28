## Why

codex-lb's runtime log level is hard-coded to INFO in `build_log_config()`, and
logs only go to stderr. An operator debugging a live instance cannot turn on
DEBUG without editing code. In Docker, the only copy of the logs is the
container's json-file log, which is deleted whenever the container is recreated
(every redeploy). Setting `CODEX_LB_LOG_LEVEL=DEBUG` looks like it should work,
but no such setting exists, so it silently does nothing.

## What Changes

- `codex-lb` (`python -m app.cli`) gains `--log-level {critical,error,warning,info,debug}`
  (default `info`, today's behavior). It sets the level for application loggers
  and uvicorn's loggers.
- `codex-lb` gains `--log-file PATH`. When given, every record that reaches
  stderr/stdout is also written to that file, rendered by the same redacting
  formatters, with size-based rotation (50 MiB x 10 backups). The parent
  directory is created if missing. An unwritable path fails startup instead of
  running without the file.
- Both are process-level CLI flags, like `--host` and `--ws-max-size`, not
  `CODEX_LB_*` settings. The settings budget is full, and log destination is a
  process concern.
- Defaults are unchanged: without the flags, output is identical to today.
- Fix: the in-process Prometheus metrics server no longer reconfigures logging.
  Its `uvicorn.Config` used uvicorn's stock `log_config` with
  `log_level="warning"`, which uvicorn applies process-wide. With
  `CODEX_LB_METRICS_ENABLED=true`, that silently dropped all access-log lines
  and reset the `uvicorn.*` loggers to uvicorn's stock handlers, detaching
  codex-lb's configured handlers (including the log file). It was found while
  verifying `--log-file` on a live instance that had no access lines at all.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `proxy-runtime-observability`: adds operator control of the runtime log level
  and an optional rotated log file that keeps the existing redaction.

## Impact

- `app/cli.py`: two flags, passed to `build_log_config()` and `uvicorn.Config`.
- `app/core/runtime_logging.py`: `build_log_config(level, log_file)`, with
  defaults matching today.
- `app/main.py`: the metrics server's `uvicorn.Config` passes
  `log_config=None, log_level=None`.
- `docker-compose.yml`: not changed upstream; operators add the flags to
  `command:`. The docs show how, using the durable data volume.
- Docs: `docs/reference/settings.md` "Process-level" section, and
  `docs/deployment/docker.md`.
