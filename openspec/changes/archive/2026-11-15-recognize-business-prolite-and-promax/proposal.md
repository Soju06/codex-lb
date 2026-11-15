## Why

Upstream usage payloads can identify Business Pro Lite as `self_serve_business_prolite` and Pro Max as `promax`. Unrecognized plans can fail usage identity validation, lose capacity estimates, and miss Pro-gated model eligibility (upstream issues #2511 and #2637).

## What Changes

- Canonicalize `self_serve_business_prolite` to the existing `prolite` plan at account and rate-limit plan boundaries.
- Recognize `promax`, preserve that identifier for display, and use the existing Pro capacity estimates and model eligibility.
- Cover usage refresh, account selection, and dashboard capacity through existing product paths.
- Keep recognized Pro-family plans in the existing coarse Pro telemetry bucket.

## Impact

- Affected specs: `usage-refresh-policy`, `telemetry`.
- No database migration, new configuration, or quota-status recovery changes.
- Pro Max capacity remains an estimate equal to Pro until upstream provides measured capacity evidence.
