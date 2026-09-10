## Why

Operators need to reserve quota on individual accounts instead of consuming the full provider allowance.

## What Changes

- Persist independent optional consumed-percentage caps for an account's actual 5h and weekly windows.
- Stop admitting new requests when either cap is reached; resume automatically when neither is reached.
- Add Accounts-page controls and cap markers on individual account quota bars without altering reported provider usage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: per-account usage-cap eligibility and persistence.
- `account-quota-presentation`: cap controls and quota-bar markers.

## Impact

Account database migration, dashboard account API, proxy selection and connection reuse, React account surfaces, and regression tests. No new dependencies or environment settings.
