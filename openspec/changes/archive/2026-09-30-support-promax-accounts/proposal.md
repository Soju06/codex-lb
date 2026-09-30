## Why

Pro Max accounts use the upstream `promax` identifier. The proxy preserves
this string but does not recognize it as a Pro-family account, assigns it
unknown-plan routing capacity, and silently omits it from estimated-credit
totals. Its observed weekly percentage and account-specific Astra Ultrafast
catalog are usable without inventing an absolute subscription allowance.

## What Changes

- Recognize and retain `promax`, with Pro-family model eligibility fallback.
- Use Pro reference capacity only as a routing heuristic for Pro Max.
- Preserve upstream percentage-only quota data and absent short windows.
- Expose incomplete estimated-credit coverage in usage summaries, scoped
  API-key pools and client usage responses.
- Show Pro 500 identity, percentage usage and explicit subtotal coverage in
  the dashboard; suppress fleet forecasts that cannot include the account.
- Verify account-specific Ultrafast eligibility through the existing catalog.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Pro Max identity, observed windows and unknown allowance.
- `account-routing`: Pro-family fallback and percentage-based availability.
- `account-pool-usage-v1-usage`: incomplete pool and upstream-limit coverage.
- `api-keys`: scoped pooled usage with unquantified accounts.
- `frontend-architecture`: plan display and truthful incomplete usage presentation.

## Impact

Additive API metadata, quota aggregation, routing-state construction and
dashboard presentation. No database migration or new configuration.
Existing Pro, Pro Lite, Plus and unknown-plan behavior remains unchanged.

## Non-goals

Changing API token prices, estimating Max allowance from price or credits,
granting Ultrafast without catalog evidence, changing context windows,
redesigning quota estimation for other plans, or deploying to production.
