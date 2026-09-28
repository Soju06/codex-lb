## Why

Force Probe can return a successful upstream response while its local health settlement fails. The load balancer reads account and usage rows in a repository session, then uses those ORM rows after session teardown has rolled back the read transaction and expired their attributes. The dashboard catches the settlement error, leaving a probing account unrecovered. This is the remaining settlement failure reported in #2410; the unsupported probe payload field was already fixed in #2496.

## What changes

- Retain independent, loaded account and usage snapshots before the settlement repository session closes.
- Preserve usage normalization and the health-observation version guard without performing database reads under the account lock.
- Exercise the dashboard Force Probe route with real repository/session teardown, absent and partial usage, and concurrent health evidence.

## Scope

The existing Force Probe settlement path, regression tests, and the `usage-refresh-policy` contract. No API, settings, schema, probe payload, or routing-policy change.
