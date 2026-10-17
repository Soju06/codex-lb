# Tasks

## Step 1: OpenSpec

- [x] Add proposal, design, and capability deltas; run `openspec validate`.

## Step 2: Capacity seed (S1)

- [x] Seed `NULL` for the four account-capacity overrides in `SettingsRepository.get_or_create`.
- [x] Update settings API integration assertions for a fresh row (override `null`, effective = env).
- [x] Add a unit test proving a later environment change is honoured by a fresh row.
- [x] Update `docs/deployment/kubernetes.md` and the `proxy-admission-control` spec context to the NULL-seed semantics.

## Step 3: Telemetry precedence (S6)

- [x] Reorder `resolve_consent` so a persisted decision wins and env applies only while undecided.
- [x] Update consent and API unit tests (env fallback while undecided, persisted wins after PUT, opt-out notice on env-active -> dashboard-disabled).
- [x] Keep the dashboard toggle usable when `source === "env"`; update the notice copy (en, ko, zh-CN) and the component test.
- [x] Update `docs/telemetry.md` and the telemetry spec context.

## Step 4: Verification

- [x] `make lint`, targeted `uv run pytest`, frontend lint/typecheck/test, `openspec validate`.
