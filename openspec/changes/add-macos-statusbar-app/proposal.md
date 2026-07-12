## Why

Operators sometimes only need a quick view of local codex-lb account availability and quota usage. Opening the full dashboard is unnecessary for checking whether accounts are active, how much 5h/weekly/monthly capacity remains, or whether the local service needs attention.

## What Changes

- Add a small macOS status bar app that talks to the local codex-lb dashboard API.
- Package the app as a DMG that can be opened locally.
- Show active account count and average visible remaining quota directly in the menu bar title.
- Show per-account cards with progress bars, status pills, plan, routing policy, quota percentages, and reset countdowns in the menu.
- Support dashboard password login, guest login, configurable server URL, manual refresh, and opening the full dashboard.

## Capabilities

### New Capabilities

- `desktop-statusbar`: Local macOS status bar visibility for codex-lb account status and usage.

### Modified Capabilities

None.

## Impact

- Adds a macOS-only local utility under `desktop/statusbar/`.
- Adds a DMG build target for local distribution.
- Reuses existing read-only dashboard API endpoints without changing backend response contracts.
