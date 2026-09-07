## Why

API key holders can see aggregate usage and request history, but they cannot confirm which key is connected, review its policy, or inspect configured limits. The grid and summary also need clearer visual hierarchy, and users need an explicit way to reconnect after a browser reload without re-entering the key.

## What Changes

- Add a privacy-safe authenticated API key profile endpoint for the self-service dashboard.
- Show API key identity metadata, lifecycle timestamps, model/reasoning/service-tier/traffic/transport policy, and typed usage limits without exposing the raw key, account assignments, source assignments, or internal routing data.
- Add an explicit “remember on this browser” option backed by browser-local storage; invalid credentials and Disconnect remove the stored secret.
- Rebalance recent-request grid column widths for the privacy-safe column set.
- Give the four usage summary cards distinct semantic accent colors.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: Extend the self-service dashboard with privacy-safe key details, limits, optional browser persistence, and presentation requirements.

## Impact

- Backend: `app/modules/key_dashboard` adds one Bearer-authenticated read-only endpoint and allowlist schema.
- Frontend: the key-dashboard data loader, schemas, page, localized copy, summary styling, and request-grid sizing change.
- Tests: backend response privacy/auth coverage and frontend details/persistence/presentation flows are extended.
- No database schema, administrator dashboard authentication, proxy routing, or API key creation behavior changes.
