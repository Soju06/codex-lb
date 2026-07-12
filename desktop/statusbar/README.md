# Codex LB Status Bar

Simple macOS status bar utility for CodexLB dashboard monitoring.

## Prerequisites

- macOS 13+
- Xcode command line tools (`xcode-select --install`)

## Build and create DMG

```bash
swiftc -O -parse-as-library -o build/CodexLBStatusBar.app/Contents/MacOS/CodexLBStatusBar StatusBarLogic.swift CodexLBStatusBar.swift -framework Cocoa -framework Foundation -framework ServiceManagement
./build-dmg.sh
```

The script builds:

- `build/CodexLBStatusBar.app` – local app bundle
- `dist/CodexLBStatusBar.dmg` – distributable installer image

## Runtime behavior

- Shows an icon in the menu bar with current quota summary in the title.
- Polls dashboard summary every 60 seconds.
- Supports admin login, guest login, dashboard URL change, launch at login, and forced refresh.

## Notes

- The server URL is stored in local `UserDefaults` under the app user session.
- Error and empty states are shown in English only (no Korean UI strings).

## Project files

- `CodexLBStatusBar.swift`: Swift source for the status bar app UI and API client.
- `build-dmg.sh`: Packaging script for building the `.dmg`.
