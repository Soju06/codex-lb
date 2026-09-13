# Tasks

## 1. Repository freshness observation

- [x] 1.1 Extend `StickyOwnerLookup` with `refresh_skip_deadline`
      (`observed_updated_at + skip window`), computed only on the fresh-row TTL
      lookup path: `updated_at` within `min(15s, 1% of TTL)`, not in the future,
      and both abandonment marker columns NULL
- [x] 1.2 Keep the deadline unset on the stale-delete recovery path, on lookups
      without a TTL, on rows carrying any abandonment marker, and on rows whose
      `updated_at` is ahead of the clock

## 2. Selection wiring

- [x] 2.1 Thread the deadline from `run_sticky_selection_path`'s per-attempt owner
      lookup through `_select_with_stickiness` onto the retention mutation; reset it
      when the raw legacy owner shadows the namespaced row, when the inner helper
      re-resolves the owner itself, and when the process seed is still missing
      (seed initialization piggybacks on the retention write)
- [x] 2.2 Revalidate the deadline at the persist site (`_sticky_refresh_write_skippable`)
      and only then omit the same-owner refresh statement — on both the non-probe
      persist path and the recovery-probe admission path (whose compensating
      restores are skipped symmetrically when nothing was written); rebinds,
      deletes, restores of actually-written rows, and seed-initializing writes
      keep writing immediately

## 3. Verification

- [x] 3.1 Unit tests: skip on fresh same-owner retention, write-through when the
      deadline is unset or lapsed at persist time, rebind/departed-owner writes never
      suppressed, grace-period retention honors the window, internal re-resolution
      resets the deadline, persist-time gate guards deletes/seed writes/non-datetime
      deadlines
- [x] 3.2 Balancer-level tests: fresh same-owner retention issues no write when the
      seed exists, a fresh thread row with a missing seed still writes and initializes
      the seed, an expired deadline writes through, and a fresh retention of a
      due-probing pinned owner skips the write on the probe admission path while
      the probe reservation still commits
- [x] 3.3 Integration tests: deadline conditions against the real repository (fresh
      row, TTL-scaled window, marker disqualification, future timestamp, no-TTL
      lookup), concurrent upserts on one `(key, kind)` keep RETURNING/self-write and
      single-row semantics, a skipped refresh never clobbers a concurrent rebind
- [x] 3.4 `uv run pytest tests/unit/test_select_with_stickiness.py
      tests/unit/test_load_balancer_concurrency.py
      tests/integration/test_proxy_sticky_sessions.py`, `uv run ruff check`,
      `uv run ruff format --check`, `make typecheck`
- [x] 3.5 `openspec validate coalesce-sticky-same-owner-refresh --strict`
