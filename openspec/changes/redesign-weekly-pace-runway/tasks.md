# Tasks

## 1. Backend — runway calculation + attribution (PR 1)

- [ ] 1.1 Add runway fields to `WeeklyCreditPaceResponse` (additive): `headroom_percent`, `headroom_credits`, `burn_rate_recent_credits_per_hour`, `depletion_eta_hours`, `next_relief_in_hours`, `next_relief_credits`, `reset_events`, `runway_status`, `saturated_account_count`, `top_api_keys`, `add_pro_accounts`
- [ ] 1.2 Implement runway math in `weekly_pace.py`: trailing-3h fleet burn, ETA, relief clustering (≥95% cohort, 1h reset-day cluster), verdict per design.md; keep legacy fields populated with current math
- [ ] 1.3 Map legacy `status` from `runway_status` (`runs_dry→danger`, `tight→ahead`, `safe→on_track`)
- [ ] 1.4 Per-key trailing-2h attribution query (repository) + wire into overview service; LIMIT + index check on `request_logs(requested_at)`
- [ ] 1.5 Stable `add_pro_accounts` from trailing-7d quota-weeks demand, gated per spec
- [ ] 1.6 Unit tests: verdict branches (runs_dry/tight/safe), relief clustering, censoring label, attribution merge/dedupe, legacy status mapping
- [ ] 1.7 Integration test: overview payload carries new fields; older-shape compatibility
- [ ] 1.8 `make lint` + `uv run ty check` + unfiltered `tests/unit`

## 2. Frontend — card redesign (PR 2, after PR 1 merges)

- [ ] 2.1 Zod schema additions (optional fields, older-backend tolerant)
- [ ] 2.2 Card rewrite: header verdict badge, four-question layout (headroom / ETA / relief / attribution), timeline bar (now → ETA marker → reset ticks)
- [ ] 2.3 Instant paint from overview; projections as refinement; fixed-footprint skeleton
- [ ] 2.4 Design alignment with existing dashboard primitives (tokens, spacing, muted palette; no bespoke one-off styles)
- [ ] 2.5 Conditional remedies: throttle-first when runs_dry; add-capacity only when gated on
- [ ] 2.6 i18n: en/ko/zh-CN strings
- [ ] 2.7 Component tests: verdict rendering, attribution list, no-projections paint, saturated floor label
- [ ] 2.8 Frontend gates (typecheck/lint/build/vitest) + before/after screenshots

## 3. Spec sync & archive

- [ ] 3.1 `openspec validate redesign-weekly-pace-runway` clean
- [ ] 3.2 After both PRs merge: `/opsx:sync` then `/opsx:archive`
