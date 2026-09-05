## 1. Gate

- [x] 1.1 Add `scripts/check_proxy_timing_seams.py` (stdlib only) with the `raw-sleep`, `raw-timeout`, `raw-task-spawn`, `raw-clock-read` and `missing-scheduler-kwarg` rules, alias and parameter-shadow handling, loop-alias resolution and the `_service_time()` / `REAL_CLOCK` / `REAL_SCHEDULER` seams
- [x] 1.2 Load `[scheduler_kwarg_required]`, `[allowances.timing]` and `[allowances.clock]` from the marked block in `openspec/specs/proxy-architecture/spec.md`; report definition failures by name without hiding module parse failures
- [x] 1.3 Report every site of an over-budget module followed by a per-module summary; fail on a listed module that is not scanned
- [x] 1.4 Add `--report` (regenerate the block from the tree) and `--explain` (every site with its rule id)

## 2. Configuration

- [x] 2.1 Add the marked `proxy-timing-seams` TOML block to `openspec/specs/proxy-architecture/spec.md`, seeded from `--report` on the `takeover/sim-harness` tree
- [x] 2.2 List every owner-less seam function whose in-repo callers pass its collaborators (`scheduler`, `clock`, `scheduler_owner`) in `[scheduler_kwarg_required]`, excluding the `stream_responses` and `_select_with_stickiness` homonyms
- [x] 2.3 Carry the `api.py` route-layer deferring-cancellation calls as that module's timing allowance rather than by rule

## 3. Tests and wiring

- [x] 3.1 Add `tests/unit/test_check_proxy_timing_seams.py`: one positive fixture per rule spelling, sanctioned shapes, definition failures, exact stderr, `--report`/`--explain`
- [x] 3.2 Pin the repository: the gate passes, every allowance equals its count, and `--report` reproduces the committed block
- [x] 3.3 Prove the negative on the real tree: one raw `asyncio.sleep` in `app/modules/proxy/_service/http_bridge/streaming.py` fails the gate with `raw-sleep`; revert the probe
- [x] 3.4 Add `uv run python scripts/check_proxy_timing_seams.py` to `Makefile` `architecture-check`
- [x] 3.5 `ruff check`, `ruff format --check`, `ty check` on the new files; `openspec validate enforce-proxy-timing-seams --strict`
