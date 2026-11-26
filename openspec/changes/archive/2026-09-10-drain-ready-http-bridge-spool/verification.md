# Backlog persistence verification

Base: `0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0`. Candidate batcher SHA-256: `b7f045f967d47631f5053c6634b76f9a8e8ac011644cef7c7cf223ddfa3ae399`. The production diff rearms the existing wake when eligible backlog remains after a bounded pass.

## Regression proof

The first public-interface regression failed for both spool formats before the implementation, then passed after the two-line fix. The batcher source did not change between the original base `069b82be3` and the refreshed base above. Ten new cases cover ordered bounded passes, sibling fairness, cancellation during a rearmed write, terminal ordering, and sibling progress after rejected or failed persistence.

The existing and new batcher suites, bridge ring lifecycle suite and HTTP bridge suite passed together: 1,169 tests. `make lint typecheck` passed, including proxy architecture, cancellation safety, timing seam and settings-tier checks. Strict OpenSpec validation passed all 65 main specs and this change.

The preserved 1,169-test run is `/tmp/codex-lb-perf-2291-proof/affected-tests.log`. The quiet log records the total rather than individual test names. The two invariant tests below passed separately during this documentation repair on the unchanged production source, 2 passed in 1.12 seconds:

| Guarantee | Test |
| --- | --- |
| Late chunk writers cannot revive an abandoned operation or grow its spool | `tests/unit/test_bridge_ring_lifecycle.py::test_abandoned_chunk_operation_fences_late_owner_chunk_writers` |
| Concurrent submissions obey the same-session request queue limit | `tests/integration/test_http_responses_bridge.py::test_v1_responses_http_bridge_enforces_queue_limit_atomically_for_same_session` |

The fake-writer source, prototype patch and separate repeat counts are mapped in [context.md](context.md#fake-writer-provenance).

## Real database measurements

Five repeats per backend, format and variant used 320 distinct 1 KiB events with batch size 32 and a 100 ms flush interval. The real durable coordinator and repository persisted each burst. The base and candidate shared all dependencies and database configuration; their execution order alternated. The timed interval ended after the last background append returned. No terminal event or forced flush accelerated it.

| Database | Format | Base median drain | Candidate median drain | Base event p95 | Candidate event p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| SQLite | rows_v1 | 994.27 ms | 69.36 ms | 993.90 ms | 70.38 ms |
| SQLite | chunks_v2 | 990.53 ms | 56.66 ms | 990.15 ms | 74.78 ms |
| PostgreSQL 18 | rows_v1 | 1189.07 ms | 235.01 ms | 1188.69 ms | 274.81 ms |
| PostgreSQL 18 | chunks_v2 | 1244.76 ms | 248.82 ms | 1244.40 ms | 257.44 ms |

Throughput uses all 1,600 timed events per backend/format/variant divided by the sum of its five burst durations in seconds. It is not 320 divided by median drain time. Terminal events occur after timing and are excluded.

| Database | Format | Base events/s | Candidate events/s |
| --- | --- | ---: | ---: |
| SQLite | rows_v1 | 320.51 | 4481.11 |
| SQLite | chunks_v2 | 322.42 | 5036.54 |
| PostgreSQL 18 | rows_v1 | 269.40 | 1260.48 |
| PostgreSQL 18 | chunks_v2 | 257.89 | 1274.68 |

These values come from `aggregate_events_s` in `/tmp/codex-lb-perf-2291-proof/persistence-summary.json`, calculated from `sqlite-persistence-results.json` and `postgres-persistence-results.json` in the same directory. The retained `persistence_benchmark.py` has SHA-256 `eb9ba3685dcdf03f88af0357221b7f4ec6bd364a372cb975d72b6b43a43a1b92`. It loads `base_batcher.py` with the base hash recorded in context and the candidate production file identified above.

All 40 runs persisted ten batches of 32 events and identical SQL cursor counts. Every run then appended a terminal event and verified exact ordered replay, completed operation state, complete spool and a replayable transcript through public coordinator methods. The change removes interval waits; it does not reduce SQL work.

## Limits

These are isolated persistence measurements, not live Codex request latency or production capacity. PostgreSQL ran in a disposable container limited to one CPU and 512 MiB. The required test database configuration uses NullPool, unlike production pooling. Host activity and connection setup affect absolute timing. Event samples within a batch share a completion time; five bursts do not establish independent tail confidence intervals. Cancellation, failure and fairness proof comes from the regression suite rather than this single-operation benchmark.

No running service or production database was changed. The disposable PostgreSQL container and its anonymous volume were removed after the measurements. Hosted CI, upstream acceptance and deployment are separate delivery gates.
