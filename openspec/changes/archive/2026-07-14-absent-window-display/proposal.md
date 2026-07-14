## Why

With upstream temporarily not reporting the 5-hour window, dashboard account summaries and API-key pooled credit bars keep displaying the last observed primary sample indefinitely: a frozen used percentage with a reset time in the past, or an optimistic `primary_remaining_percent = 100.0` default when no primary row exists at all. Routing and the aggregated `x-codex-*` surfaces already expire elapsed samples (adaptive-rate-limit-windows); operator-facing displays were deliberately deferred and now diverge from what selection actually uses.

## What Changes

- Account summaries treat a display window whose last sample's reset has elapsed as absent: used/remaining percent, remaining credits, reset timestamp, and window duration become null for that window. Status derivation keeps its existing inputs, so displayed status stays aligned with routing.
- The optimistic `primary_remaining_percent = 100.0` default for accounts without a primary row is removed; missing data displays as absent.
- API-key pooled primary credits expire elapsed samples like the aggregated header path, and `pooled_remaining_percent_primary` is null when no live (unexpired) primary sample exists across the pooled accounts.
- No frontend code changes: the UI already renders null windows as absent (the weekly-only precedent).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: pooled primary credit semantics for expired/absent windows.
- `frontend-architecture`: account quota displays hide expired windows instead of freezing.

## Impact

- Code: `app/modules/accounts/mappers.py`, `app/modules/api_keys/service.py`
- Tests: `tests/unit/test_account_mappers.py` (or equivalent), `tests/integration/test_api_keys_api.py`
- Specs: `openspec/specs/api-keys/spec.md`, `openspec/specs/frontend-architecture/spec.md`
