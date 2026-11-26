The measurement at upstream 069b82be3 used 320 synthetic 1 KiB events, batch size 32, flush interval 100 ms and a fake writer sleeping 1 ms per persistence call. Three baseline burst runs took 914.64 to 928.95 ms. Three separate prototype burst runs took 15.82 to 16.05 ms, with ten writes in every run. This measures scheduling, not database throughput or end-to-end response speed.

PR #1997 changes terminal persistence timeout and cleanup. This change affects the independent background loop and starts from current main. The integration candidate 971322208 and live source 1a58a006 contain the same sleeping loop, with separately owned terminal/scheduler changes around it.

## Fake-writer provenance

The original performance report identifies the measured source as `app/modules/proxy/http_bridge_event_batcher.py` at `069b82be3ceda8e468094f03aee0884061b2b43e`. Its SHA-256 is `f0ebb4f1aef203a7197e7c519b4ad40102a6350d4896876cd1ffbcc26450c49f`, unchanged at refreshed base `0f6a31c56ac30804ca1c0fac27ca02c6f59bf2b0`.

The retained `/tmp/codex-lb-perf-stream/bench.py` loads that file's AST, replaces application imports with format constants and a record type, and measures the base class with a fake writer. Its `RearmedBatcher._run` subclass supplies the prototype: after each bounded pass, `if await self._operation_ids_to_flush(): self._wake.set()`. This is the same two-line scheduling change committed in `6fbb8b080864ae13a4d76e9cde8d1bc63053fd19`. The resulting production file has SHA-256 `b7f045f967d47631f5053c6634b76f9a8e8ac011644cef7c7cf223ddfa3ae399`; the initial fake-writer candidate was the subclass, not that later file.

Artifact mapping:

| Artifact | SHA-256 | Measurements |
| --- | --- | --- |
| `/tmp/codex-lb-perf-stream/bench.py` | `868f72b2476f8efae807ce7ad3fa226f5dceef1f053ee14713ebf2d87f8b5284` | Source loader, fake writer and prototype subclass |
| `/tmp/codex-lb-perf-stream/results.json` | `4b283f965d453497f164fc4417da497eafe40c2070348be7a5dd06e6cca65141` | Three base burst entries in `spool` with `mode=burst`, and three prototype entries in `rearmed_burst` |

The script's `ROOT` names the original worktree, which has since been removed. The retained JSON does not embed the source hash; the pin comes from the original report, and the hash above was verified against Git during this repair. To reproduce the original comparison, set `ROOT` to a checkout pinned to the base above before running with Python 3.13.5. Do not load the current candidate as the baseline. Drain timing includes the observer's 5 ms polling delay. Recorded burst throughput ranges are 344.5 to 349.9 events/s for the base and 19,933.0 to 20,229.7 events/s for the prototype.

These six fake-writer burst runs are separate from the five repeats per backend, format and variant in the 40-run real-database proof in [verification.md](verification.md).
