## 1. Implementation

- [x] 1.1 Default CLI HTTP binding to `0.0.0.0`.
- [x] 1.2 Add `CODEX_LB_HOST` as the project-specific environment override while preserving `--host` precedence.
- [x] 1.3 Make the Docker entrypoint honor the same host and port environment defaults.

## 2. Documentation

- [x] 2.1 Document the default remote-capable bind address and local-only override.
- [x] 2.2 Add remote dashboard timeout troubleshooting notes.

## 3. Verification

- [x] 3.1 Add unit coverage for default bind behavior.
- [x] 3.2 Add unit coverage for `CODEX_LB_HOST` and CLI override precedence.
