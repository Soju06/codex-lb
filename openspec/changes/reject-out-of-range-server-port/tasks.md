## 1. CLI regression coverage

- [x] 1.1 Add real `cli.main()` regressions for both `--port` and `PORT` that reject `-1`, `65536`, and `70000` with the supported range before `_load_uvicorn` is called.
- [x] 1.2 Add real `cli.main()` boundary coverage for both input forms that accepts and forwards `0` and `65535`, while retaining explicit-flag precedence.
- [x] 1.3 Run the focused new regressions before the production fix and record the expected out-of-range failures as red evidence.

## 2. Listener-port validation

- [x] 2.1 Update only `_parse_server_port` to reject converted integers outside the inclusive range `0..65535` with an actionable `--port/PORT` error.
- [x] 2.2 Run the CLI regression group and focused CLI unit suite to confirm invalid inputs fail before startup and valid behavior remains green.
- [x] 2.3 Run focused lint and type checks for the changed Python files.

## 3. OpenSpec verification

- [x] 3.1 Strictly validate the active change and main specifications on the focused worktree content.
- [x] 3.2 Verify implementation, tests, tasks, delta spec, design, and context are coherent; retain the change with lifecycle `active_through_merge` and do not archive it.
