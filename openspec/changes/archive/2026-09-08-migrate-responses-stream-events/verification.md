# Verification

- Shared Python/Rust corpus: 48 cases, including aliases, all SSE newline forms,
  errors in SDK/native modes, malformed input, unknown fields, escaped JSON,
  duplicate keys, mixed line endings, arrays and serialization handoffs.
- Python/native adapter, fixtures, packaging contract, Codex client, direct/routed
  integration and proxy utility tests: **1,878 passed**. Public native/missing-helper
  differential coverage runs each shared case through direct/routed HTTP and both
  SDK modes. Real helper tests cover fragmented large events, early terminal close,
  cancellation, idle deadlines, original-byte limits and no replay.
- Additional invalid metadata checks cover UTF-8 type-size limits and intermediate
  handoff flags: **10 focused checks passed**. Queue tests account for metadata bytes and release them on drain.
- Rust: format, Clippy with warnings denied, all **22** workspace tests, locked
  release worker build and cargo-deny passed. Cargo-deny reports existing duplicate
  dependency warnings; advisory, ban, license and source checks pass.
- Full repository Ruff lint/format and ty passed. Proxy architecture, cancellation
  safety and timing seam checks passed. All 58 strict OpenSpec specifications and
  simplicity budgets passed; the change is synced and archived.

## Synthetic stream measurement

Same release helper in both modes; loopback HTTP and real IPC. Baseline requests
native framing and runs Python normalization; interpreted mode requests native
interpretation. Each request contains 2,048 small deltas. Four warmups and 20
measured requests per mode/shape, alternating order, under CPython 3.13 with uvloop.
The shared host was busy; these figures are not production capacity evidence.
Parent CPU excludes the helper. No model delay is included.

| Shape | Python median / p95 ms | Native median / p95 ms | Python parent CPU ms | Native parent CPU ms |
|---|---:|---:|---:|---:|
| Canonical | 436.4 / 501.6 | 448.6 / 493.5 | 123.9 | 127.4 |
| Data only | 495.6 / 534.8 | 498.8 / 582.4 | 141.7 | 131.9 |
| Legacy alias | 493.8 / 571.9 | 537.7 / 621.7 | 164.4 | 133.7 |

The alias case reduces parent CPU by 18.7% but increases median elapsed time by
8.9%; canonical adds 2.8% median and parent CPU. An earlier asyncio-loop run also
showed no elapsed-time improvement (canonical/data-only/alias medians changed
414.5→420.1, 390.0→429.2, 403.2→450.6 ms). The result supports ownership migration,
not a speed claim. IPC overhead and full application benchmarks remain follow-up
work before claiming improved throughput.

## Review parity check

CodeRabbit questioned escaped `error` keys on canonical blocks. Python's existing
canonical fast path also checks the literal `"error"` substring and returns these
blocks unchanged in both SDK modes. Parsing first only in Rust would diverge.
Added shared canonical and data-only escaped-key cases: the canonical case stays
unchanged; the data-only case uses Python error conversion. Rust fixture parity
and all 10 Python direct/routed/SDK checks passed. After merging current main,
481 native/public Responses contract tests and all 58 strict specs passed.
