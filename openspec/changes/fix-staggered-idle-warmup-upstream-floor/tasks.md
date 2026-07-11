## Tasks

- [x] Wire `limit_warmup_exhausted_threshold_percent` into the staggered idle
      path (`_build_staggered_idle_candidate` in
      `app/modules/limit_warmup/service.py`).
- [x] Remove the hardcoded `_STAGGERED_IDLE_USED_PERCENT_FLOOR` constant;
      the idle gate now uses the configurable threshold.
- [x] Add regression test asserting `used_percent = 1.0` with the threshold
      set to 1.0 qualifies for staggered idle warm-up.
- [x] Update rejection test to use a threshold lower than the test
      `used_percent` so the gate correctly rejects.
- [x] Update OpenSpec proposal and spec to reflect the configurable
      threshold being wired to both warm-up modes.
- [x] Run `uv run ruff check` and `uv run ruff format --check`.
- [x] Run `uv run pytest tests/unit/test_limit_warmup.py`.
- [x] Run `openspec validate fix-staggered-idle-warmup-upstream-floor --strict`.
