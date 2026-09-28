## ADDED Requirements

### Requirement: Operators set the runtime log level from the server command

The server command MUST accept `--log-level` with one of `critical`, `error`,
`warning`, `info`, or `debug`, defaulting to `info`. The chosen level MUST
apply to codex-lb's application (`app.*`) loggers. uvicorn's loggers and
third-party library loggers MUST stay at `info`, or at the chosen level when
that is stricter, so that uvicorn's protocol-level tracing (WebSocket
handshakes and frames) is not logged at any setting. An unknown level MUST
make the command exit with a usage error before the server starts.

#### Scenario: Debug level emits debug records

- **WHEN** the server starts with `--log-level debug`
- **THEN** application `DEBUG` records are emitted

#### Scenario: Default level is unchanged

- **WHEN** the server starts without `--log-level`
- **THEN** application `DEBUG` records are not emitted
- **AND** `INFO` records are emitted as before

#### Scenario: Startup states the active log configuration

- **WHEN** the server starts
- **THEN** a record from `app.cli` states the configured level, the log file path (or `none`), and the rotation
- **AND** it is logged at INFO, or at the configured level when that is stricter, so it appears at every level
- **AND** with `--log-file` that record is also written to the file

#### Scenario: Debug does not include uvicorn protocol tracing

- **GIVEN** the server started with `--log-level debug` and `--log-file`
- **WHEN** a client opens a WebSocket carrying a distinctive request header value
- **THEN** neither the stream nor the file contains that value or any uvicorn DEBUG record
- **AND** codex-lb `app.*` DEBUG records are still written

#### Scenario: Unknown level is rejected

- **WHEN** the server command is given `--log-level verbose`
- **THEN** it exits with a usage error and no server starts

### Requirement: Operators can also write logs to a rotated file

The server command MUST accept `--log-file PATH`. When given, every record the
server emits to stderr or stdout, including access-log lines, MUST also be
written to `PATH`, rendered by the same redacting formatter as the stream
output, and the file MUST rotate by size (50 MiB, 10 backups) through a single
rotation sequence shared by application and access records. The parent
directory MUST be created when missing. If the file cannot be opened, startup
MUST fail instead of continuing without the file. Without `--log-file`, no log
file is written.

#### Scenario: File receives application and access logs

- **GIVEN** the server started with `--log-file /data/logs/codex-lb.log`
- **WHEN** a client requests `GET /health`
- **THEN** the file contains the startup log lines and the access-log line for that request

#### Scenario: Metrics server does not reset server logging

- **GIVEN** metrics are enabled and the server started with `--log-file`
- **WHEN** a client requests `GET /health` after the metrics server has started
- **THEN** the access-log line for that request appears on stdout and in the file
- **AND** `uvicorn.*` records keep codex-lb's configured handlers and formatter

#### Scenario: File output is redacted like stream output

- **GIVEN** the server started with `--log-file`
- **WHEN** a request line containing URL userinfo (`https://user:<secret>@host`) is access-logged
- **THEN** neither the file nor the stream contains `<secret>`
- **AND** the file line matches the stream line

#### Scenario: Application and access records share one rotation

- **GIVEN** a `--log-file` whose size limit is reached by interleaved application and access records
- **WHEN** the file rotates
- **THEN** the newest application record and the newest access record are in the same current file
- **AND** each record appears exactly once across the rotated files, in order

#### Scenario: Missing directory is created

- **GIVEN** the parent directory of the `--log-file` path does not exist
- **WHEN** the server starts
- **THEN** the directory is created and the file is written

#### Scenario: Unwritable path fails startup

- **GIVEN** the `--log-file` path cannot be opened for writing
- **WHEN** the server starts
- **THEN** it exits with an error and no server starts
