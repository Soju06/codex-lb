## 1. Specs

- [x] 1.1 Add an OpenSpec delta for command-line runtime lifecycle control.
- [x] 1.2 Document the new foreground/background CLI workflow in the README.

## 2. Implementation

- [x] 2.1 Add `serve`, `start`, `status`, and `shutdown` CLI command handling while keeping bare invocation backward compatible.
- [x] 2.2 Add background process management helpers for PID metadata, readiness checks, and signal-based shutdown.

## 3. Verification

- [x] 3.1 Add unit coverage for CLI parsing and lifecycle helper behavior.
- [x] 3.2 Run targeted CLI regression tests.
