## 1. Implementation

- [x] 1.1 `build_log_config(level="info", log_file=None)` in `app/core/runtime_logging.py`: apply `level` to the root, `uvicorn`, `uvicorn.error`, and `uvicorn.access` loggers; when `log_file` is set, create its parent directory and attach rotating file handlers (50 MiB x 10, UTF-8, colors off) using the same formatter classes. Verify that `tests/unit/test_structured_logging.py` still passes unchanged.
- [x] 1.2 `app/cli.py`: add `--log-level` (choices, default `info`) and `--log-file`, and pass them to `build_log_config()` and `uvicorn.Config(log_level=...)`. Verify with 2.1.

- [x] 1.3 In `app/main.py`, create the metrics server's `uvicorn.Config` with `log_config=None, log_level=None` so it cannot reset process-wide logging. Verify with the metrics case in 2.1.

- [x] 1.4 Log one INFO line from `app.cli` after uvicorn applies the config: `Logging configured level=… file=… rotation=…`. Verify with the debug-and-file and defaults integration tests.

## 2. Tests

- [x] 2.1 Integration test that launches the real server command as a subprocess on a free port with `--log-level debug --log-file <tmp>/logs/codex-lb.log`: the missing directory is created; after `GET /health`, the file has DEBUG records, the access line, and startup lines; a credentialed URL in an access line appears redacted identically in the stream and the file. Without the flags: no file and no DEBUG lines. `--log-level verbose` exits 2. An unwritable file path exits non-zero. With `CODEX_LB_METRICS_ENABLED=true`, access lines still reach stdout and the file.

## 4. Review follow-ups (local review, 2026-09-28)

- [x] 4.1 Clamp uvicorn's loggers (dictConfig levels and the `uvicorn.Config` log level) to info or stricter; `--log-level debug` raises only `app.*`, not uvicorn's protocol-level WebSocket tracing. Verify with `test_debug_level_excludes_uvicorn_protocol_tracing` (fails on the previous code, passes now).
- [x] 4.2 Replace the two `RotatingFileHandler`s on one path with one handler and a per-record `FileLogFormatter`, so rotation has one owner. Verify with `test_single_handler_owns_rotation` (fails on the previous code, passes now).
- [x] 4.3 Log the startup configuration line at INFO, or at the configured level when stricter, and announce the configured `app.*` level rather than uvicorn's clamped level. Verify with `test_startup_line_survives_stricter_levels` (fails on the previous code, passes now).

## 3. Docs and validation

- [x] 3.1 Document both flags in `docs/reference/settings.md` (process-level section) and show a compose `command:` example with the data volume in `docs/deployment/docker.md`, including where to keep the log file. Verify the pages link to `openspec/specs/proxy-runtime-observability/`.
- [x] 3.2 Run `npx --yes @fission-ai/openspec@1.11.0 validate add-cli-log-level-and-file --strict`, `make lint`, and `uv run ty check`; all pass.
- [x] 3.3 Deploy to the home instance with `--log-level debug --log-file /var/lib/codex-lb/logs/codex-lb.log`, and confirm the file exists on the host under the `codex-lb-data` volume and keeps its contents across a container recreate.
