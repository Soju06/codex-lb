# Redesign weekly pace card: runway vs relief

## Why

The 2026-08-16 pool-exhaustion incident proved the current "Weekly credits pace"
card answers the wrong question. It compares actual burn against a synthetic
linear schedule nobody follows, so it reads "31% over planned usage" during
normal bursty operation and stayed uninformative while the fleet was actually
running dry. Operators treat it as noise.

Diagnosed defects (verified against `app/modules/dashboard/weekly_pace.py` and
incident data):

1. **Fictional baseline** — `scheduled_used_percent` assumes uniform linear
   burn across the window. Real usage is bursty; a gap vs an imaginary plan is
   not actionable, and the ±5% status thresholds keep the card permanently in
   a warning state (alarm fatigue).
2. **The operative question is missing** — "do we run dry BEFORE the next
   reset returns capacity?" The internal simulation already models resets but
   the card never surfaces when relief arrives or how much it returns.
3. **Noisy recommendation** — "add ~N accounts" divides an instantaneous
   6h-EWMA shortfall by 50,400, so it swings wildly day to day. Account
   purchases are weekly decisions; an hourly-volatile number destroys trust.
4. **No attribution** — fleet totals only. During the incident the actionable
   unit was a single API key (73% of requests); the card gave no way to see it.
5. **Censoring ignored** — `used_percent` saturates at 100, so demand-derived
   claims understate need exactly when the fleet is clipped.

## What Changes

- Replace the card's model: linear-schedule pace comparison → **runway vs
  relief** (headroom, depletion ETA, next reset relief, survives-to-relief
  verdict).
- Status semantics change from schedule-gap thresholds to the relief verdict
  (`safe` / `tight` / `runs_dry`); legacy fields stay populated for one release.
- Add per-API-key burn attribution (trailing 2h) to the overview payload and
  card.
- Rebase the add-capacity recommendation on trailing-7d demand in
  quota-weeks (stable), gated behind a persistent shortfall; keep
  `throttle_to_percent` as the first remedy.
- Frontend card redesign: timeline bar (now → ETA → reset ticks), instant
  paint from the overview payload (no projections-fetch gate), shared
  dashboard design primitives.

## Impact

- Affected specs: `frontend-architecture` (Dashboard weekly credits pace
  requirement)
- Affected code:
  - `app/modules/dashboard/weekly_pace.py` (calculation rewrite)
  - `app/modules/dashboard/schemas.py`, `service.py` (new fields, attribution
    query wiring)
  - `app/modules/usage/repository.py` or dashboard repository (per-key 2h
    rollup query)
  - `frontend/src/features/dashboard/components/weekly-credits-pace-card.tsx`,
    `schemas.ts`, `utils.ts` (+ tests)
- Two stacked PRs: backend (calc + API), frontend (card). One OpenSpec change
  governs both.
- Wire compat: all existing response fields remain for one release; new fields
  are additive. `GET /api/dashboard/overview` and `/projections` shapes stay
  backward-compatible.
