## 1. Implementation

- [x] 1.1 Add a scan cursor with 3-byte straddle overlap to `_iter_sse_events`; scan via `_find_sse_separator(buffer, start)`
- [x] 1.2 Raise `_SSE_READ_CHUNK_SIZE` to 16 KiB

## 2. Tests

- [x] 2.1 Regression: separator straddling a read boundary; multiple events after a large partial (cursor reset)
- [x] 2.2 Existing SSE framing/limit/idle-timeout tests stay green

## 3. Validation

- [x] 3.1 Measured: 8 MiB event 70.5 s → 0.18 s; linear scaling verified
- [x] 3.2 `openspec validate --specs`, `ruff`, `ty`, proxy test suites
