## Context

`build_log_config()` copies uvicorn's `LOGGING_CONFIG`, swaps in codex-lb's
redacting formatters (text or JSON), and attaches the stderr handler to the
root logger at a fixed INFO. uvicorn's own `uvicorn` and `uvicorn.access`
loggers keep separate handlers with `propagate: false`.

## Goals / Non-Goals

**Goals:** DEBUG on demand, and a durable, rotated copy of exactly what the
server already prints, with the same redaction.

**Non-Goals:** per-logger levels, syslog/remote shipping, a `CODEX_LB_*`
setting, or changing the default output.

## Decisions

**CLI flags, not settings.** The `Settings` field budget
(`.github/simplicity-budgets.toml`) is at its ceiling. Where logs go and how
verbose they are is a property of the process launch, like `--host` and
`--ws-max-size`, which the compose `command:` and Helm args already carry.
Unlike their neighbours, the flags have no environment fallback, because
`check_settings_tiers.py` caps `os.getenv` reads in `app/cli.py`.

**Same formatters, separate handlers.** The file gets its own `default` and
`access` formatter instances, with colors off, and `RotatingFileHandler`s
attached to the root, `uvicorn`, and `uvicorn.access` loggers. Rendering and
redaction therefore stay identical to stderr, and a single flag covers both
app and access logs.

**Fixed rotation (50 MiB x 10).** DEBUG output is large, and an unrotated file
on a data volume can fill the disk that also holds the database. Fixed values
avoid two more knobs; operators who need something else can point the file at
logrotate-managed storage.

**Fail fast on an unwritable path.** `logging.config.dictConfig` raises when
the handler cannot open its file. Startup aborts, so an operator never believes
logs are persisted when they are not.

**The level targets `app.*` only (revised after review).** The first version
also raised uvicorn's loggers. At DEBUG, uvicorn emits protocol-level tracing
(WebSocket handshakes and every frame), which is transport noise rather than
codex-lb diagnostics and dwarfs the application's records. uvicorn is now
clamped to info or stricter, both in the dictConfig and in the level passed to
`uvicorn.Config`, which uvicorn applies to its own loggers.

**One file handler (revised after review).** The first version attached
separate `RotatingFileHandler`s for application and access records to the same
path, so each rotated independently and records scattered across files. One
handler with a formatter that dispatches per record gives rotation a single
owner.

## Risks / Trade-offs

- [Log files hold what the stream holds] → The file is rendered by the same
  formatters as stderr, so it carries exactly what the process already prints,
  now durably. The docs point operators at operator-only storage and note
  which values the existing redaction policy masks at which level.
- [Disk use] → Bounded at about 550 MiB by rotation.
