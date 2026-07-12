## Why

Local operators who keep the dashboard bound to loopback should not have to re-authenticate every day when dashboard auth is still enabled. The existing configurable session lifetime can already express a longer session, but the default remains 12 hours and applying a 1-year lifetime everywhere would be unsafe for dashboards exposed through LAN, cloud, or reverse-proxy paths.

## What Changes

- Change the persisted dashboard session lifetime default from 12 hours to 1 year.
- Migrate existing dashboard settings rows that still carry the old 12-hour default to the new 1-year default, while preserving customized values.
- Resolve the effective session TTL at issuance time: configured lifetimes above 30 days apply only to direct loopback requests in standard dashboard auth mode, or to loopback dashboard URLs when the explicit loopback-host-header override is enabled for localhost-published deployments. Remote/proxy/trusted-header requests fall back to 12 hours.
- Keep shorter configured lifetimes intact, including on remote requests.

## Impact

- Affected backend modules: dashboard auth session issuance, dashboard settings defaults, and the dashboard settings migration chain.
- Existing localhost-only deployments that never customized the setting move to annual dashboard sessions after migration.
- Non-local or proxy-authenticated dashboard sessions do not silently receive the long TTL.
