## Why

Dashboard account views currently place two unrelated values in one `Credits` field. A numeric upstream `creditsBalance`, including `0.0`, hides the account's calculated remaining subscription quota and makes healthy accounts appear to have no capacity.

## What Changes

- Show calculated subscription quota and upstream purchased credits as separate account metrics in both card and list views.
- Sort each list column by its own metric instead of mixing the two sources.
- Preserve `Unlimited` only for the purchased-credit metric.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: dashboard account views MUST distinguish calculated subscription quota from purchased upstream credits.

## Impact

Frontend account cards/list, their localized labels, and focused component tests. No backend or schema change.
