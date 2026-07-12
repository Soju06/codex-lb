## 1. macOS status bar app

- [x] 1.1 Add a native macOS status bar app that defaults to `http://127.0.0.1:2455`.
- [x] 1.2 Fetch `GET /api/dashboard-auth/session` and `GET /api/dashboard/overview?timeframe=7d`.
- [x] 1.3 Render account active count, quota status, and reset timing in the menu.
- [x] 1.4 Add admin password login, guest login, configurable server URL, manual refresh, dashboard open, and quit actions.
- [x] 1.5 Render account cards, status pills, and quota progress bars in the menu.
- [x] 1.6 Show available reset-credit count and nearest expiry on account cards.
- [x] 1.7 Match dashboard quota warning thresholds and track colors.
- [x] 1.8 Show refreshing and last-successful-refresh feedback in the account panel header.
- [x] 1.9 Replace the visible account panel in place when refresh completes.
- [x] 1.10 Replace the lowest-account menu bar summary with active-account averages and the compact count suffix.
- [x] 1.11 Keep routing and status badge widths stable across in-place refreshes.
- [x] 1.12 Match dashboard routing badge colors and symbols for burn-first and preserve policies.
- [x] 1.13 Add a native macOS launch-at-login menu toggle.

## 2. Packaging

- [x] 2.1 Add a local build script that creates a `.app` bundle.
- [x] 2.2 Add a local build script step that signs the bundle ad hoc and packages a DMG.
- [x] 2.3 Add a Makefile target for the DMG build.

## 3. Validation

- [x] 3.1 Build the DMG locally.
- [x] 3.2 Validate the OpenSpec change and all main specs with `@fission-ai/openspec` 1.6.0 in strict mode.
- [x] 3.3 Run the status bar logic self-test and rebuild the DMG.
