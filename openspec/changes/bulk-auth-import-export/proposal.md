# Proposal: bulk-auth-import-export

## Why

Account import currently accepts only one `auth.json` file at a time, leaves already-expired access tokens untouched until a later runtime refresh path happens, and offers no way to export the current account auth payloads from the dashboard. That creates unnecessary operator work and makes backup or migration flows slower and less reliable.

## What Changes

- Allow importing multiple `auth.json` files in one dashboard action.
- Refresh imported auth immediately when the uploaded access token is expired but the refresh token can still mint a fresh token set.
- Add a dashboard export endpoint that downloads a zip archive containing one current `auth.json` payload per stored account.

## Impact

- Backend accounts API adds a batch import endpoint and auth zip export endpoint.
- Frontend Accounts page import dialog and actions must support selecting multiple files and downloading the export zip.
- Tests must cover partial import success, import-time refresh, and zip archive content.
