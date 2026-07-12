## Tasks

- [x] Add `limit_warmup_idle_threshold_percent` setting (default 1.0) with DB
      migration, model column, settings service/schema/repository/API wiring.
- [x] Wire `limit_warmup_idle_threshold_percent` into the staggered idle path
      (`_build_staggered_idle_candidate` in `app/modules/limit_warmup/service.py`).
- [x] Change the staggered idle gate from hardcoded `> 0.0` to
      `> idle_threshold_percent`.
- [x] Add regression test asserting `used_percent = 1.0` with the idle
      threshold set to 1.0 qualifies for staggered idle warm-up.
- [x] Fix reset-confirmed warm-up to require a minimum 60-second `reset_at`
      forward jump, preventing upstream timestamp jitter from triggering
      false warm-ups.
- [x] Add regression test asserting a 1-second `reset_at` jitter does not
      trigger a warm-up.
- [x] Redesign the warm-up settings UI into two side-by-side cards:
      "Reset-confirmed warm-up" (with "Min usage %") and "Staggered idle
      warm-up" (with "Max usage %").
- [x] Rename threshold labels for clarity: "Exhausted at %" → "Min usage %",
      "Idle at %" → "Max usage %".
- [x] Shorten "Save warmup model" button to "Save".
- [x] Update frontend tests to enable warm-up before testing limit warm-up
      controls.
- [x] Run `uv run ruff check` and `uv run ruff format --check`.
- [x] Run `uv run pytest tests/unit/test_limit_warmup.py`.
- [x] Run frontend tests (`bun run test`).
- [x] Run `openspec validate fix-staggered-idle-warmup-upstream-floor --strict`.
